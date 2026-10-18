// SPDX-License-Identifier: GPL-2.0-only
/*
 * Realtek RTL9300 switch port LEDs under software control
 *
 * The RTL9300 drives up to four LEDs per switch port over its LED interface,
 * serial or scanned. Each one normally shows a hardware function (link,
 * speed, activity) chosen by the LED set of the port, but software can take
 * it over and switch it on or off instead. Boards use that for port LEDs
 * that have no hardware function, such as the PoE status LED of a PoE
 * switch.
 *
 * Software control has a mode per LED, kept separately for copper and fibre
 * media, and the LED controller picks the new modes up when it is told to
 * load them. Both media are set alike here. The registers are part of the
 * switch register block; the node's reg gives their offset in it.
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

/* Offsets from reg, which is SW_LED_LOAD (0xcc48 on the RTL9301) */
#define RTL9300_LED_SW_LOAD			0x00
#define   RTL9300_LED_SW_LOAD_START		BIT(0)
#define RTL9300_LED_PORT_SW_EN_CTRL(port)	(0x04 + ((port) / 8) * 4)
#define   RTL9300_LED_PORT_SW_EN(port, led)	BIT(((port) % 8) * 4 + (led))
#define RTL9300_LED_PORT_SW_CTRL(port)		(0x14 + (port) * 4)
/* 3-bit mode per LED: 0 off, 7 on, copper LEDs from bit 0, fibre from 12 */
#define   RTL9300_LED_SW_MODE(led)		((GENMASK(2, 0) | GENMASK(14, 12)) << (3 * (led)))

#define RTL9300_LED_PORTS			28
#define RTL9300_LED_PER_PORT			4

struct rtl9300_port_leds {
	struct regmap *map;
	u32 base;
};

struct rtl9300_port_led {
	struct led_classdev cdev;
	struct rtl9300_port_leds *ctrl;
	u32 port;
	u32 led;
};

static int rtl9300_port_led_load(struct rtl9300_port_leds *ctrl)
{
	return regmap_write(ctrl->map, ctrl->base + RTL9300_LED_SW_LOAD,
			    RTL9300_LED_SW_LOAD_START);
}

static int rtl9300_port_led_set(struct led_classdev *cdev,
				enum led_brightness brightness)
{
	struct rtl9300_port_led *pled = container_of(cdev, struct rtl9300_port_led, cdev);
	struct rtl9300_port_leds *ctrl = pled->ctrl;
	u32 mask = RTL9300_LED_SW_MODE(pled->led);
	int ret;

	ret = regmap_update_bits(ctrl->map, ctrl->base + RTL9300_LED_PORT_SW_CTRL(pled->port),
				 mask, brightness ? mask : 0);
	if (ret)
		return ret;

	return rtl9300_port_led_load(ctrl);
}

/* Hand the LED back to its hardware function */
static void rtl9300_port_led_release(void *data)
{
	struct rtl9300_port_led *pled = data;
	struct rtl9300_port_leds *ctrl = pled->ctrl;

	regmap_clear_bits(ctrl->map, ctrl->base + RTL9300_LED_PORT_SW_EN_CTRL(pled->port),
			  RTL9300_LED_PORT_SW_EN(pled->port, pled->led));
	rtl9300_port_led_load(ctrl);
}

static int rtl9300_port_led_add(struct device *dev, struct rtl9300_port_leds *ctrl,
				struct fwnode_handle *fwnode)
{
	struct led_init_data init_data = { .fwnode = fwnode };
	struct rtl9300_port_led *pled;
	enum led_brightness brightness;
	u32 reg[2], sw_en, val;
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

	pled->ctrl = ctrl;
	pled->port = reg[0];
	pled->led = reg[1];
	sw_en = ctrl->base + RTL9300_LED_PORT_SW_EN_CTRL(pled->port);

	switch (led_init_default_state_get(fwnode)) {
	case LEDS_DEFSTATE_ON:
		brightness = LED_ON;
		break;
	case LEDS_DEFSTATE_KEEP:
		ret = regmap_read(ctrl->map, sw_en, &val);
		if (ret)
			return ret;
		brightness = LED_OFF;
		if (val & RTL9300_LED_PORT_SW_EN(pled->port, pled->led)) {
			ret = regmap_read(ctrl->map,
					  ctrl->base + RTL9300_LED_PORT_SW_CTRL(pled->port),
					  &val);
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

	ret = regmap_set_bits(ctrl->map, sw_en, RTL9300_LED_PORT_SW_EN(pled->port, pled->led));
	if (ret)
		return ret;

	ret = devm_add_action_or_reset(dev, rtl9300_port_led_release, pled);
	if (ret)
		return ret;

	ret = rtl9300_port_led_load(ctrl);
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
	struct rtl9300_port_leds *ctrl;
	int ret;

	ctrl = devm_kzalloc(dev, sizeof(*ctrl), GFP_KERNEL);
	if (!ctrl)
		return -ENOMEM;

	ctrl->map = syscon_node_to_regmap(dev_of_node(dev->parent));
	if (IS_ERR(ctrl->map))
		return dev_err_probe(dev, PTR_ERR(ctrl->map), "no switch regmap\n");

	ret = device_property_read_u32(dev, "reg", &ctrl->base);
	if (ret)
		return dev_err_probe(dev, ret, "no reg\n");

	device_for_each_child_node_scoped(dev, child) {
		ret = rtl9300_port_led_add(dev, ctrl, child);
		if (ret)
			return ret;
	}

	return 0;
}

static const struct of_device_id rtl9300_port_leds_of_match[] = {
	{ .compatible = "realtek,rtl9301-port-leds" },
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
