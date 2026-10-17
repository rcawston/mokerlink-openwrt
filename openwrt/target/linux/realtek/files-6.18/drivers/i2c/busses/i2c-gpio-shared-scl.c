// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Bit-banged I2C buses that share one SCL line
 *
 * Some boards wire several I2C buses to GPIOs with one common SCL line and
 * a separate SDA line per bus. The vendor firmware bit-bangs them in turn.
 * i2c-gpio cannot describe this, because every bus would request the same
 * SCL GPIO. This driver requests SCL once and registers one adapter per SDA
 * line. Transfers on the adapters are serialized, so only one bus drives
 * SCL at a time; devices on the other buses see SCL toggle while their SDA
 * stays high, which is never a START condition, and ignore it.
 *
 * Copyright (C) 2026 The OpenWrt contributors
 */

#include <linux/delay.h>
#include <linux/device.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c-algo-bit.h>
#include <linux/i2c.h>
#include <linux/jiffies.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/slab.h>

struct i2c_gpio_shared_scl;

struct i2c_gpio_shared_scl_bus {
	struct i2c_gpio_shared_scl *parent;
	struct gpio_desc *sda;
	struct i2c_algo_bit_data bit_data;
	struct i2c_adapter adap;
	bool registered;
};

struct i2c_gpio_shared_scl {
	struct device *dev;
	struct gpio_desc *scl;
	struct mutex lock;	/* one transfer on the shared SCL at a time */
	unsigned int num_buses;
	struct i2c_gpio_shared_scl_bus buses[] __counted_by(num_buses);
};

static void i2c_gpio_shared_scl_setsda(void *data, int state)
{
	struct i2c_gpio_shared_scl_bus *bus = data;

	gpiod_set_value_cansleep(bus->sda, state);
}

static int i2c_gpio_shared_scl_getsda(void *data)
{
	struct i2c_gpio_shared_scl_bus *bus = data;

	return gpiod_get_value_cansleep(bus->sda);
}

static void i2c_gpio_shared_scl_setscl(void *data, int state)
{
	struct i2c_gpio_shared_scl_bus *bus = data;

	gpiod_set_value_cansleep(bus->parent->scl, state);
}

static int i2c_gpio_shared_scl_getscl(void *data)
{
	struct i2c_gpio_shared_scl_bus *bus = data;

	return gpiod_get_value_cansleep(bus->parent->scl);
}

static int i2c_gpio_shared_scl_pre_xfer(struct i2c_adapter *adap)
{
	struct i2c_gpio_shared_scl_bus *bus = i2c_get_adapdata(adap);

	mutex_lock(&bus->parent->lock);

	return 0;
}

static void i2c_gpio_shared_scl_post_xfer(struct i2c_adapter *adap)
{
	struct i2c_gpio_shared_scl_bus *bus = i2c_get_adapdata(adap);

	mutex_unlock(&bus->parent->lock);
}

static void i2c_gpio_shared_scl_del_buses(struct i2c_gpio_shared_scl *priv)
{
	unsigned int i;

	for (i = 0; i < priv->num_buses; i++)
		if (priv->buses[i].registered)
			i2c_del_adapter(&priv->buses[i].adap);
}

static int i2c_gpio_shared_scl_add_bus(struct i2c_gpio_shared_scl *priv,
				       struct i2c_gpio_shared_scl_bus *bus,
				       struct fwnode_handle *child,
				       u32 udelay, u32 timeout_ms)
{
	struct device *dev = priv->dev;
	struct i2c_algo_bit_data *bit_data = &bus->bit_data;
	struct i2c_adapter *adap = &bus->adap;
	u32 reg;
	int ret;

	ret = fwnode_property_read_u32(child, "reg", &reg);
	if (ret)
		return dev_err_probe(dev, ret, "%pfwP: missing reg\n", child);

	bus->parent = priv;
	bus->sda = devm_fwnode_gpiod_get(dev, child, "sda",
					 GPIOD_OUT_HIGH_OPEN_DRAIN,
					 fwnode_get_name(child));
	if (IS_ERR(bus->sda))
		return dev_err_probe(dev, PTR_ERR(bus->sda),
				     "%pfwP: cannot get SDA GPIO\n", child);

	bit_data->data = bus;
	bit_data->setsda = i2c_gpio_shared_scl_setsda;
	bit_data->getsda = i2c_gpio_shared_scl_getsda;
	bit_data->setscl = i2c_gpio_shared_scl_setscl;
	bit_data->getscl = i2c_gpio_shared_scl_getscl;
	bit_data->pre_xfer = i2c_gpio_shared_scl_pre_xfer;
	bit_data->post_xfer = i2c_gpio_shared_scl_post_xfer;
	bit_data->udelay = udelay;
	bit_data->timeout = msecs_to_jiffies(timeout_ms);

	adap->owner = THIS_MODULE;
	adap->algo_data = bit_data;
	adap->dev.parent = dev;
	device_set_node(&adap->dev, child);
	snprintf(adap->name, sizeof(adap->name), "%s SDA%u", dev_name(dev), reg);
	i2c_set_adapdata(adap, bus);

	ret = i2c_bit_add_bus(adap);
	if (ret)
		return dev_err_probe(dev, ret, "%pfwP: cannot add bus\n", child);

	bus->registered = true;

	return 0;
}

static int i2c_gpio_shared_scl_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct i2c_gpio_shared_scl *priv;
	u32 udelay = 5, timeout_ms = 100;
	unsigned int num_buses, i = 0;
	int ret;

	num_buses = device_get_child_node_count(dev);
	if (!num_buses)
		return dev_err_probe(dev, -EINVAL, "no buses\n");

	priv = devm_kzalloc(dev, struct_size(priv, buses, num_buses), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->dev = dev;
	priv->num_buses = num_buses;
	ret = devm_mutex_init(dev, &priv->lock);
	if (ret)
		return ret;

	/* Same properties as i2c-gpio */
	device_property_read_u32(dev, "i2c-gpio,delay-us", &udelay);
	device_property_read_u32(dev, "i2c-gpio,timeout-ms", &timeout_ms);

	priv->scl = devm_gpiod_get(dev, "scl", GPIOD_OUT_HIGH_OPEN_DRAIN);
	if (IS_ERR(priv->scl))
		return dev_err_probe(dev, PTR_ERR(priv->scl), "cannot get SCL GPIO\n");

	platform_set_drvdata(pdev, priv);

	device_for_each_child_node_scoped(dev, child) {
		ret = i2c_gpio_shared_scl_add_bus(priv, &priv->buses[i++], child,
						  udelay, timeout_ms);
		if (ret) {
			i2c_gpio_shared_scl_del_buses(priv);
			return ret;
		}
	}

	dev_info(dev, "%u buses on shared SCL, %u us half period\n", num_buses, udelay);

	return 0;
}

static void i2c_gpio_shared_scl_remove(struct platform_device *pdev)
{
	i2c_gpio_shared_scl_del_buses(platform_get_drvdata(pdev));
}

static const struct of_device_id i2c_gpio_shared_scl_of_match[] = {
	{ .compatible = "i2c-gpio-shared-scl" },
	{ /* sentinel */ }
};
MODULE_DEVICE_TABLE(of, i2c_gpio_shared_scl_of_match);

static struct platform_driver i2c_gpio_shared_scl_driver = {
	.driver = {
		.name = "i2c-gpio-shared-scl",
		.of_match_table = i2c_gpio_shared_scl_of_match,
	},
	.probe = i2c_gpio_shared_scl_probe,
	.remove = i2c_gpio_shared_scl_remove,
};

/* Registered early, like i2c-gpio, so the buses exist before their clients */
static int __init i2c_gpio_shared_scl_init(void)
{
	return platform_driver_register(&i2c_gpio_shared_scl_driver);
}
subsys_initcall(i2c_gpio_shared_scl_init);

static void __exit i2c_gpio_shared_scl_exit(void)
{
	platform_driver_unregister(&i2c_gpio_shared_scl_driver);
}
module_exit(i2c_gpio_shared_scl_exit);

MODULE_DESCRIPTION("Bit-banged I2C buses sharing one SCL line");
MODULE_LICENSE("GPL");
