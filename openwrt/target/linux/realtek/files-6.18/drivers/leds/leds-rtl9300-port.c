// SPDX-License-Identifier: GPL-2.0-only
/*
 * Realtek RTL9300 switch port LEDs under software control
 *
 * The RTL9300 drives up to four LEDs per switch port over its serial LED
 * interface. Each one normally shows a hardware function (link, speed,
 * activity) chosen by the LED set of the port, but software can take it
 * over and switch it on or off instead. Boards use that for port LEDs that
 * have no hardware function, such as the PoE status LED of a PoE switch.
 *
 * Software control has a mode per LED, kept separately for copper and fibre
 * media, and the LED controller picks the new modes up when it is told to
 * load them. Both media are set alike here.
 *
 * Copyright (C) 2026 The OpenWrt contributors
 */

#include <linux/bits.h>
#include <linux/leds.h>
#include <linux/mfd/syscon.h>
#include <linux/mod_devicetable.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <linux/property.h>
#include <linux/regmap.h>

#define RTL9300_LED_SW_LOAD			0xcc48
#define   RTL9300_LED_SW_LOAD_START		BIT(0)
#define RTL9300_LED_PORT_SW_EN_CTRL(port)	(0xcc4c + ((port) / 8) * 4)
#define   RTL9300_LED_PORT_SW_EN(port, led)	BIT(((port) % 8) * 4 + (led))
#define RTL9300_LED_PORT_SW_CTRL(port)		(0xcc5c + (port) * 4)
/* 3-bit mode per LED: 0 off, 7 on, copper LEDs from bit 0, fibre from 12 */
#define   RTL9300_LED_SW_MODE(led)		((GENMASK(2, 0) | GENMASK(14, 12)) << (3 * (led)))

#define RTL9300_LED_PORTS			28
#define RTL9300_LED_PER_PORT			4

struct rtl9300_port_led {
	struct led_classdev cdev;
	struct regmap *map;
	u32 port;
	u32 led;
};

static int rtl9300_port_led_load(struct regmap *map)
{
	return regmap_write(map, RTL9300_LED_SW_LOAD, RTL9300_LED_SW_LOAD_START);
}

static int rtl9300_port_led_set(struct led_classdev *cdev,
				enum led_brightness brightness)
{
	struct rtl9300_port_led *pled = container_of(cdev, struct rtl9300_port_led, cdev);
	u32 mask = RTL9300_LED_SW_MODE(pled->led);
	int ret;

	ret = regmap_update_bits(pled->map, RTL9300_LED_PORT_SW_CTRL(pled->port),
				 mask, brightness ? mask : 0);
	if (ret)
		return ret;

	return rtl9300_port_led_load(pled->map);
}

/* Hand the LED back to its hardware function */
static void rtl9300_port_led_release(void *data)
{
	struct rtl9300_port_led *pled = data;

	regmap_clear_bits(pled->map, RTL9300_LED_PORT_SW_EN_CTRL(pled->port),
			  RTL9300_LED_PORT_SW_EN(pled->port, pled->led));
	rtl9300_port_led_load(pled->map);
}

static int rtl9300_port_led_add(struct device *dev, struct regmap *map,
				struct fwnode_handle *fwnode)
{
	struct led_init_data init_data = { .fwnode = fwnode };
	struct rtl9300_port_led *pled;
	enum led_brightness brightness;
	u32 reg[2], val;
	int ret;

	ret = fwnode_property_read_u32_array(fwnode, "reg", reg, ARRAY_SIZE(reg));
	if (ret)
		return dev_err_probe(dev, ret, "%pfw: bad reg\n", fwnode);
	if (reg[0] >= RTL9300_LED_PORTS || reg[1] >= RTL9300_LED_PER_PORT)
		return dev_err_probe(dev, -EINVAL, "%pfw: no LED %u of port %u\n",
				     fwnode, reg[1], reg[0]);

	pled = devm_kzalloc(dev, sizeof(*pled), GFP_KERNEL);
	if (!pled)
		return -ENOMEM;

	pled->map = map;
	pled->port = reg[0];
	pled->led = reg[1];

	switch (led_init_default_state_get(fwnode)) {
	case LEDS_DEFSTATE_ON:
		brightness = LED_ON;
		break;
	case LEDS_DEFSTATE_KEEP:
		ret = regmap_read(map, RTL9300_LED_PORT_SW_EN_CTRL(pled->port), &val);
		if (ret)
			return ret;
		brightness = LED_OFF;
		if (val & RTL9300_LED_PORT_SW_EN(pled->port, pled->led)) {
			ret = regmap_read(map, RTL9300_LED_PORT_SW_CTRL(pled->port), &val);
			if (ret)
				return ret;
			if (val & RTL9300_LED_SW_MODE(pled->led))
				brightness = LED_ON;
		}
		break;
	default:
		brightness = LED_OFF;
		break;
	}

	ret = rtl9300_port_led_set(&pled->cdev, brightness);
	if (ret)
		return ret;

	ret = regmap_set_bits(map, RTL9300_LED_PORT_SW_EN_CTRL(pled->port),
			      RTL9300_LED_PORT_SW_EN(pled->port, pled->led));
	if (ret)
		return ret;

	ret = devm_add_action_or_reset(dev, rtl9300_port_led_release, pled);
	if (ret)
		return ret;

	ret = rtl9300_port_led_load(map);
	if (ret)
		return ret;

	pled->cdev.brightness = brightness;
	pled->cdev.max_brightness = LED_ON;
	pled->cdev.brightness_set_blocking = rtl9300_port_led_set;

	return devm_led_classdev_register_ext(dev, &pled->cdev, &init_data);
}

static int rtl9300_port_leds_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct regmap *map;
	int ret;

	map = syscon_node_to_regmap(dev_of_node(dev->parent));
	if (IS_ERR(map))
		return dev_err_probe(dev, PTR_ERR(map), "no switch regmap\n");

	device_for_each_child_node_scoped(dev, child) {
		ret = rtl9300_port_led_add(dev, map, child);
		if (ret)
			return ret;
	}

	return 0;
}

static const struct of_device_id rtl9300_port_leds_of_match[] = {
	{ .compatible = "realtek,rtl9300-port-leds" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl9300_port_leds_of_match);

static struct platform_driver rtl9300_port_leds_driver = {
	.probe = rtl9300_port_leds_probe,
	.driver = {
		.name = "leds-rtl9300-port",
		.of_match_table = rtl9300_port_leds_of_match,
	},
};
module_platform_driver(rtl9300_port_leds_driver);

MODULE_DESCRIPTION("Realtek RTL9300 switch port LEDs under software control");
MODULE_LICENSE("GPL");
