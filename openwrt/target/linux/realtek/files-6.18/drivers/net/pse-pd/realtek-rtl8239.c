// SPDX-License-Identifier: GPL-2.0-only
/*
 * Realtek RTL8239 PoE PSE controller driver
 *
 * The RTL8239 is an 8-channel IEEE 802.3af/at/bt PSE controller. Its register
 * map follows TI's TPS2388x (operating mode 0x12, power status 0x10, class
 * 0x0c, channel current and voltage from 0x30), but it is driven differently:
 *
 * - Each 4-channel half answers on its own I2C address, the second one at
 *   the first plus one, with 8-bit register access.
 * - The chip runs Realtek firmware that the host sends at every power-up,
 *   in one write to a broadcast address shared by all RTL8239s on the bus.
 * - A port is switched on by putting its channels in auto mode and off by
 *   switching them off. Detection, classification, 4-pair operation and the
 *   power allocation are left to the firmware.
 *
 * This follows the vendor firmware of the MokerLink POE-10G07110GSM, which
 * has no RTL8239 datasheet behind it; the meaning of the Realtek-specific
 * registers (0xe8, 0xec, 0xf4) is inferred from that firmware.
 *
 * Copyright (C) 2026 The OpenWrt contributors
 */

#include <linux/bitfield.h>
#include <linux/delay.h>
#include <linux/firmware.h>
#include <linux/i2c.h>
#include <linux/iopoll.h>
#include <linux/jiffies.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/pse-pd/pse.h>
#include <linux/slab.h>
#include <linux/unaligned.h>

#define RTL8239_MAX_CHANS		8
#define RTL8239_QUAD_CHANS		4

#define RTL8239_REG_PW_STATUS		0x10	/* PE 3:0, PG 7:4 */
#define RTL8239_REG_OP_MODE		0x12	/* 2 bits per channel */
#define   RTL8239_OP_MODE_AUTO		0x3
#define RTL8239_REG_RESET		0x1a
#define   RTL8239_RESET_INIT		0x20
#define   RTL8239_RESET_DOWNLOAD	0x3f
#define RTL8239_REG_ID			0x1b
#define   RTL8239_ID_MFR		GENMASK(7, 3)
#define   RTL8239_ID_MFR_REALTEK	7
#define   RTL8239_ID_REV		GENMASK(2, 0)
#define RTL8239_REG_DET_CLASS		0x0c	/* + channel; class 7:4 */
#define   RTL8239_CLASS			GENMASK(7, 4)
#define   RTL8239_CLASS_0		0xc
#define RTL8239_REG_TEMP		0x2c
#define RTL8239_REG_CHAN_CURRENT	0x30	/* + 4 * channel, 16 bit LE */
#define RTL8239_REG_CHAN_VOLTAGE	0x32	/* + 4 * channel, 15 bit LE */
#define RTL8239_REG_CHIP_TYPE		0xe8
#define   RTL8239_TYPE_RTL8239C		2
#define RTL8239_REG_FW_STATUS		0xec
#define   RTL8239_FW_DOWNLOAD		0x03
#define   RTL8239_FW_READY		BIT(2)
#define   RTL8239_FW_RUNNING		BIT(5)
#define RTL8239_REG_FW_DATA		0xf4

/* All RTL8239s on the bus take the firmware at this address */
#define RTL8239_BROADCAST_ADDR		0x60

#define RTL8239_FW_MAGIC		0x8239
#define RTL8239_FW_HDR_LEN		16
#define RTL8239_FW_RUN_TIMEOUT_MS	1000
#define RTL8239_FW_TRIES		3
#define RTL8239_FW_CHECK_INTERVAL	(30 * HZ)

/* 30.52 uA per LSB, 1.953 mV per LSB, as the vendor firmware scales them */
#define RTL8239_UA_PER_LSB_X4096	125000
#define RTL8239_UV_PER_LSB_X512		1000000

struct rtl8239_pi {
	u8 chan[2];		/* chip channels 0-7, chan[1] only for 4-pair */
	bool is_4p;
};

struct rtl8239_priv {
	struct i2c_client *quad[2];	/* channels 0-3 and 4-7 */
	struct pse_controller_dev pcdev;
	struct rtl8239_pi pi[RTL8239_MAX_CHANS];
	const struct firmware *fw;
	unsigned long next_fw_check;
	unsigned long delivering;	/* PIs delivering at the last poll */
	u8 chip_type;
};

static struct rtl8239_priv *to_rtl8239(struct pse_controller_dev *pcdev)
{
	return container_of(pcdev, struct rtl8239_priv, pcdev);
}

static struct i2c_client *rtl8239_quad(struct rtl8239_priv *priv, u8 chan)
{
	return priv->quad[chan / RTL8239_QUAD_CHANS];
}

static int rtl8239_read(struct rtl8239_priv *priv, int quad, u8 reg)
{
	return i2c_smbus_read_byte_data(priv->quad[quad], reg);
}

static int rtl8239_write(struct rtl8239_priv *priv, int quad, u8 reg, u8 val)
{
	return i2c_smbus_write_byte_data(priv->quad[quad], reg, val);
}

/* Operating mode bits of a PI's channels, per quad */
static void rtl8239_pi_mode_mask(struct rtl8239_priv *priv, int id, u8 mask[2])
{
	struct rtl8239_pi *pi = &priv->pi[id];
	int i;

	mask[0] = 0;
	mask[1] = 0;
	for (i = 0; i < (pi->is_4p ? 2 : 1); i++) {
		u8 chan = pi->chan[i];

		mask[chan / RTL8239_QUAD_CHANS] |=
			RTL8239_OP_MODE_AUTO << (2 * (chan % RTL8239_QUAD_CHANS));
	}
}

static int rtl8239_pi_set_mode(struct rtl8239_priv *priv, int id, bool on)
{
	u8 mask[2];
	int quad, ret;

	rtl8239_pi_mode_mask(priv, id, mask);
	for (quad = 0; quad < 2; quad++) {
		if (!mask[quad])
			continue;

		ret = rtl8239_read(priv, quad, RTL8239_REG_OP_MODE);
		if (ret < 0)
			return ret;

		ret = rtl8239_write(priv, quad, RTL8239_REG_OP_MODE,
				    on ? ret | mask[quad] : ret & ~mask[quad]);
		if (ret)
			return ret;
	}

	return 0;
}

static int rtl8239_pi_enable(struct pse_controller_dev *pcdev, int id)
{
	return rtl8239_pi_set_mode(to_rtl8239(pcdev), id, true);
}

static int rtl8239_pi_disable(struct pse_controller_dev *pcdev, int id)
{
	return rtl8239_pi_set_mode(to_rtl8239(pcdev), id, false);
}

static int rtl8239_pi_is_enabled(struct rtl8239_priv *priv, int id)
{
	u8 mask[2];
	int quad, ret;

	rtl8239_pi_mode_mask(priv, id, mask);
	for (quad = 0; quad < 2; quad++) {
		if (!mask[quad])
			continue;

		ret = rtl8239_read(priv, quad, RTL8239_REG_OP_MODE);
		if (ret < 0)
			return ret;
		if ((ret & mask[quad]) != mask[quad])
			return 0;
	}

	return 1;
}

/* Whether any channel of the PI delivers power (power enable and good) */
static int rtl8239_pi_is_delivering(struct rtl8239_priv *priv, int id)
{
	struct rtl8239_pi *pi = &priv->pi[id];
	int i, ret;

	for (i = 0; i < (pi->is_4p ? 2 : 1); i++) {
		u8 ch = pi->chan[i] % RTL8239_QUAD_CHANS;

		ret = i2c_smbus_read_byte_data(rtl8239_quad(priv, pi->chan[i]),
					       RTL8239_REG_PW_STATUS);
		if (ret < 0)
			return ret;
		if ((ret & BIT(ch)) && (ret & BIT(ch + 4)))
			return 1;
	}

	return 0;
}

static int rtl8239_pi_get_admin_state(struct pse_controller_dev *pcdev, int id,
				      struct pse_admin_state *admin_state)
{
	int ret = rtl8239_pi_is_enabled(to_rtl8239(pcdev), id);

	if (ret < 0)
		return ret;

	admin_state->c33_admin_state = ret ? ETHTOOL_C33_PSE_ADMIN_STATE_ENABLED :
					     ETHTOOL_C33_PSE_ADMIN_STATE_DISABLED;
	return 0;
}

static int rtl8239_pi_get_pw_status(struct pse_controller_dev *pcdev, int id,
				    struct pse_pw_status *pw_status)
{
	struct rtl8239_priv *priv = to_rtl8239(pcdev);
	int ret;

	ret = rtl8239_pi_is_delivering(priv, id);
	if (ret < 0)
		return ret;
	if (ret) {
		pw_status->c33_pw_status = ETHTOOL_C33_PSE_PW_D_STATUS_DELIVERING;
		return 0;
	}

	ret = rtl8239_pi_is_enabled(priv, id);
	if (ret < 0)
		return ret;

	pw_status->c33_pw_status = ret ? ETHTOOL_C33_PSE_PW_D_STATUS_SEARCHING :
					 ETHTOOL_C33_PSE_PW_D_STATUS_DISABLED;
	return 0;
}

/* Class of the PD on the PI's first channel; 0 when none was found */
static int rtl8239_pi_get_pw_class(struct pse_controller_dev *pcdev, int id)
{
	struct rtl8239_priv *priv = to_rtl8239(pcdev);
	u8 chan = priv->pi[id].chan[0];
	int ret, class;

	ret = i2c_smbus_read_byte_data(rtl8239_quad(priv, chan),
				       RTL8239_REG_DET_CLASS + chan % RTL8239_QUAD_CHANS);
	if (ret < 0)
		return ret;

	class = FIELD_GET(RTL8239_CLASS, ret);
	return class >= 1 && class <= 8 ? class : 0;
}

static int rtl8239_read_word(struct i2c_client *client, u8 reg)
{
	int lo, hi;

	lo = i2c_smbus_read_byte_data(client, reg);
	if (lo < 0)
		return lo;

	hi = i2c_smbus_read_byte_data(client, reg + 1);
	if (hi < 0)
		return hi;

	return hi << 8 | lo;
}

static int rtl8239_chan_uV(struct rtl8239_priv *priv, u8 chan)
{
	int ret;

	ret = rtl8239_read_word(rtl8239_quad(priv, chan),
				RTL8239_REG_CHAN_VOLTAGE + 4 * (chan % RTL8239_QUAD_CHANS));
	if (ret < 0)
		return ret;

	return DIV_ROUND_CLOSEST((u64)(ret & 0x7fff) * RTL8239_UV_PER_LSB_X512, 512);
}

static int rtl8239_chan_uA(struct rtl8239_priv *priv, u8 chan)
{
	int ret;

	ret = rtl8239_read_word(rtl8239_quad(priv, chan),
				RTL8239_REG_CHAN_CURRENT + 4 * (chan % RTL8239_QUAD_CHANS));
	if (ret < 0)
		return ret;

	return DIV_ROUND_CLOSEST((u64)ret * RTL8239_UA_PER_LSB_X4096, 4096);
}

static int rtl8239_pi_get_voltage(struct pse_controller_dev *pcdev, int id)
{
	struct rtl8239_priv *priv = to_rtl8239(pcdev);

	return rtl8239_chan_uV(priv, priv->pi[id].chan[0]);
}

static int rtl8239_pi_get_actual_pw(struct pse_controller_dev *pcdev, int id)
{
	struct rtl8239_priv *priv = to_rtl8239(pcdev);
	struct rtl8239_pi *pi = &priv->pi[id];
	u64 mW = 0;
	int i, uV, uA;

	for (i = 0; i < (pi->is_4p ? 2 : 1); i++) {
		uA = rtl8239_chan_uA(priv, pi->chan[i]);
		if (uA < 0)
			return uA;
		if (!uA)
			continue;

		uV = rtl8239_chan_uV(priv, pi->chan[i]);
		if (uV < 0)
			return uV;

		/* mW = uV * uA / 10^9 */
		mW += DIV_ROUND_CLOSEST_ULL((u64)uV * uA, 1000000000);
	}

	return mW;
}

/* Check the image as the vendor firmware builds it: magic, 32-bit sum zero */
static int rtl8239_fw_validate(struct rtl8239_priv *priv, const struct firmware *fw)
{
	struct device *dev = priv->pcdev.dev;
	u32 sum = 0;
	size_t i;

	if (fw->size < RTL8239_FW_HDR_LEN || fw->size % 4 || fw->size >= U16_MAX) {
		dev_err(dev, "firmware: bad size %zu\n", fw->size);
		return -EINVAL;
	}

	if (get_unaligned_le16(fw->data + 2) != RTL8239_FW_MAGIC) {
		dev_err(dev, "firmware: not an RTL8239 image\n");
		return -EINVAL;
	}

	for (i = 0; i < fw->size; i += 4)
		sum += get_unaligned_le32(fw->data + i);
	if (sum) {
		dev_err(dev, "firmware: bad checksum\n");
		return -EINVAL;
	}

	return 0;
}

/* One write of the whole image to the broadcast address */
static int rtl8239_fw_send(struct rtl8239_priv *priv)
{
	const struct firmware *fw = priv->fw;
	struct i2c_msg msg = {
		.addr = RTL8239_BROADCAST_ADDR,
		.len = fw->size + 1,
	};
	int ret;

	msg.buf = kmalloc(msg.len, GFP_KERNEL);
	if (!msg.buf)
		return -ENOMEM;

	msg.buf[0] = RTL8239_REG_FW_DATA;
	memcpy(msg.buf + 1, fw->data, fw->size);
	ret = i2c_transfer(priv->quad[0]->adapter, &msg, 1);
	kfree(msg.buf);

	return ret == 1 ? 0 : ret < 0 ? ret : -EIO;
}

/*
 * Reset the chip and load the firmware into it, as the vendor firmware does,
 * then wait for it to run. Both quads come up with all channels off.
 */
static int rtl8239_fw_load_once(struct rtl8239_priv *priv)
{
	int quad, ret, val;

	for (quad = 0; quad < 2; quad++) {
		ret = rtl8239_write(priv, quad, RTL8239_REG_RESET, RTL8239_RESET_INIT);
		if (ret)
			return ret;
		fsleep(10 * USEC_PER_MSEC);
		ret = rtl8239_write(priv, quad, RTL8239_REG_OP_MODE, 0);
		if (ret)
			return ret;
	}

	ret = rtl8239_write(priv, 0, RTL8239_REG_FW_STATUS, RTL8239_FW_DOWNLOAD);
	if (ret)
		return ret;
	usleep_range(1000, 2000);

	ret = rtl8239_read(priv, 0, RTL8239_REG_FW_STATUS);
	if (ret < 0)
		return ret;
	if (!(ret & RTL8239_FW_READY)) {
		ret = rtl8239_write(priv, 0, RTL8239_REG_RESET, RTL8239_RESET_DOWNLOAD);
		if (ret)
			return ret;
		fsleep(10 * USEC_PER_MSEC);
		ret = rtl8239_write(priv, 0, RTL8239_REG_FW_STATUS, RTL8239_FW_DOWNLOAD);
		if (ret)
			return ret;
		usleep_range(1000, 2000);
	}

	ret = rtl8239_fw_send(priv);
	if (ret)
		return ret;
	fsleep(10 * USEC_PER_MSEC);

	ret = read_poll_timeout(rtl8239_read, val,
				val < 0 || (val & RTL8239_FW_RUNNING),
				10 * USEC_PER_MSEC, RTL8239_FW_RUN_TIMEOUT_MS * USEC_PER_MSEC,
				false, priv, 0, RTL8239_REG_FW_STATUS);
	if (ret)
		return ret;

	return val < 0 ? val : 0;
}

static int rtl8239_fw_load(struct rtl8239_priv *priv)
{
	struct device *dev = priv->pcdev.dev;
	int i, ret = -EIO;

	for (i = 0; i < RTL8239_FW_TRIES; i++) {
		ret = rtl8239_fw_load_once(priv);
		if (!ret) {
			dev_info(dev, "firmware running (%zu bytes)\n", priv->fw->size);
			return 0;
		}
		dev_warn(dev, "firmware load failed: %d\n", ret);
	}

	return ret;
}

static const char *rtl8239_fw_name(struct rtl8239_priv *priv)
{
	const char *name;

	if (device_property_read_string(priv->pcdev.dev, "firmware-name", &name))
		name = priv->chip_type == RTL8239_TYPE_RTL8239C ?
		       "realtek/rtl8239c-bt.bin" : "realtek/rtl8239-bt.bin";

	return name;
}

/*
 * Look for the firmware, quietly when it is not there. The image belongs to
 * Realtek and is not redistributable; it has to be copied out of the vendor
 * firmware of the device. Returns true when a valid image is held.
 */
static bool rtl8239_get_fw(struct rtl8239_priv *priv)
{
	const struct firmware *fw;

	if (firmware_request_nowarn(&fw, rtl8239_fw_name(priv), priv->pcdev.dev))
		return false;

	if (rtl8239_fw_validate(priv, fw)) {
		release_firmware(fw);
		return false;
	}

	priv->fw = fw;
	return true;
}

/*
 * Every RTL8239_FW_CHECK_INTERVAL: load the firmware once it has been
 * installed, or reload it if the chip stopped running it, then restore the
 * PIs, which the reload switched off.
 */
static void rtl8239_fw_watchdog(struct rtl8239_priv *priv)
{
	struct pse_controller_dev *pcdev = &priv->pcdev;
	int ret, i;

	if (time_before(jiffies, priv->next_fw_check))
		return;
	priv->next_fw_check = jiffies + RTL8239_FW_CHECK_INTERVAL;

	if (!priv->fw) {
		if (!rtl8239_get_fw(priv))
			return;
		dev_info(pcdev->dev, "found %s\n", rtl8239_fw_name(priv));
	} else {
		ret = rtl8239_read(priv, 0, RTL8239_REG_FW_STATUS);
		if (ret < 0 || (ret & RTL8239_FW_RUNNING))
			return;
		dev_warn(pcdev->dev, "firmware stopped (status 0x%02x), reloading\n", ret);
	}

	if (rtl8239_fw_load(priv))
		return;

	for (i = 0; i < pcdev->nr_lines; i++)
		if (pcdev->pi[i].admin_state_enabled)
			rtl8239_pi_set_mode(priv, i, true);
}

/* Polled: report PIs that started or stopped delivering power */
static int rtl8239_map_event(int irq, struct pse_controller_dev *pcdev,
			     unsigned long *notifs, unsigned long *notifs_mask)
{
	struct rtl8239_priv *priv = to_rtl8239(pcdev);
	int i, ret;

	rtl8239_fw_watchdog(priv);

	for (i = 0; i < pcdev->nr_lines; i++) {
		if (!pcdev->pi[i].np)
			continue;

		ret = rtl8239_pi_is_delivering(priv, i);
		if (ret < 0)
			return ret;
		if (!!ret == test_bit(i, &priv->delivering))
			continue;

		__assign_bit(i, &priv->delivering, ret);
		__set_bit(i, notifs_mask);
		notifs[i] = ret ? ETHTOOL_C33_PSE_EVENT_DETECTION :
				  ETHTOOL_C33_PSE_EVENT_DISCONNECTION;
	}

	return 0;
}

/*
 * Map each PI to its channels: one channel, or two consecutive ones (0+1 or
 * 2+3 of a quad) for a 4-pair port, as the firmware pairs them.
 */
static int rtl8239_setup_pi_matrix(struct pse_controller_dev *pcdev)
{
	struct rtl8239_priv *priv = to_rtl8239(pcdev);
	struct device *dev = pcdev->dev;
	int i, j, n, chan;
	u32 used = 0;

	for (i = 0; i < pcdev->nr_lines; i++) {
		struct pse_pi *pi = &pcdev->pi[i];

		if (!pi->np)
			continue;

		n = pi->pairset[1].np ? 2 : 1;
		for (j = 0; j < n; j++) {
			struct device_node *np = pi->pairset[j].np;
			u32 reg;

			if (!np || !of_node_name_eq(np, "channel") ||
			    of_property_read_u32(np, "reg", &reg) ||
			    reg >= RTL8239_MAX_CHANS || (used & BIT(reg))) {
				dev_err(dev, "PI %d: bad or reused channel\n", i);
				return -EINVAL;
			}
			used |= BIT(reg);
			priv->pi[i].chan[j] = reg;
		}

		chan = priv->pi[i].chan[0];
		if (n == 2 && (chan % 2 || priv->pi[i].chan[1] != chan + 1)) {
			dev_err(dev, "PI %d: 4-pair needs channels 0+1, 2+3, 4+5 or 6+7\n", i);
			return -EINVAL;
		}
		priv->pi[i].is_4p = n == 2;
	}

	return 0;
}

static const struct pse_controller_ops rtl8239_ops = {
	.setup_pi_matrix = rtl8239_setup_pi_matrix,
	.pi_enable = rtl8239_pi_enable,
	.pi_disable = rtl8239_pi_disable,
	.pi_get_admin_state = rtl8239_pi_get_admin_state,
	.pi_get_pw_status = rtl8239_pi_get_pw_status,
	.pi_get_pw_class = rtl8239_pi_get_pw_class,
	.pi_get_actual_pw = rtl8239_pi_get_actual_pw,
	.pi_get_voltage = rtl8239_pi_get_voltage,
};

static const struct pse_irq_desc rtl8239_poll_desc = {
	.name = "rtl8239-poll",
	.map_event = rtl8239_map_event,
};

static void rtl8239_release_fw(void *data)
{
	struct rtl8239_priv *priv = data;

	release_firmware(priv->fw);
}

static void rtl8239_unregister(void *data)
{
	i2c_unregister_device(data);
}

/*
 * Switch on every channel the device tree lists, before the controller is
 * registered: consumers read the initial admin state from the hardware.
 * Like an unmanaged PoE switch, the chip then powers only valid PDs; ports
 * can be switched off with ethtool.
 */
static int rtl8239_enable_channels(struct rtl8239_priv *priv)
{
	struct device_node *channels;
	u8 mask[2] = { 0, 0 };
	int quad, ret;
	u32 reg;

	channels = of_get_child_by_name(priv->pcdev.dev->of_node, "channels");
	if (!channels)
		return 0;

	for_each_child_of_node_scoped(channels, np) {
		if (of_property_read_u32(np, "reg", &reg) || reg >= RTL8239_MAX_CHANS)
			continue;
		mask[reg / RTL8239_QUAD_CHANS] |=
			RTL8239_OP_MODE_AUTO << (2 * (reg % RTL8239_QUAD_CHANS));
	}
	of_node_put(channels);

	for (quad = 0; quad < 2; quad++) {
		if (!mask[quad])
			continue;

		ret = rtl8239_read(priv, quad, RTL8239_REG_OP_MODE);
		if (ret < 0)
			return ret;

		ret = rtl8239_write(priv, quad, RTL8239_REG_OP_MODE, ret | mask[quad]);
		if (ret)
			return ret;
	}

	return 0;
}

static int rtl8239_probe(struct i2c_client *client)
{
	struct device *dev = &client->dev;
	struct rtl8239_priv *priv;
	int ret, i, id, temp;

	if (!i2c_check_functionality(client->adapter, I2C_FUNC_I2C |
				     I2C_FUNC_SMBUS_BYTE_DATA))
		return -ENXIO;

	priv = devm_kzalloc(dev, sizeof(*priv), GFP_KERNEL);
	if (!priv)
		return -ENOMEM;

	priv->pcdev.dev = dev;
	priv->quad[0] = client;
	priv->quad[1] = i2c_new_ancillary_device(client, "quad1", client->addr + 1);
	if (IS_ERR(priv->quad[1]))
		return dev_err_probe(dev, PTR_ERR(priv->quad[1]),
				     "cannot claim the second address\n");
	ret = devm_add_action_or_reset(dev, rtl8239_unregister, priv->quad[1]);
	if (ret)
		return ret;

	for (i = 0; i < 2; i++) {
		id = rtl8239_read(priv, i, RTL8239_REG_ID);
		if (id < 0)
			return dev_err_probe(dev, id, "no answer at 0x%02x\n",
					     priv->quad[i]->addr);
		if (FIELD_GET(RTL8239_ID_MFR, id) != RTL8239_ID_MFR_REALTEK ||
		    !FIELD_GET(RTL8239_ID_REV, id))
			return dev_err_probe(dev, -ENODEV, "unknown ID 0x%02x at 0x%02x\n",
					     id, priv->quad[i]->addr);
	}

	ret = rtl8239_read(priv, 0, RTL8239_REG_CHIP_TYPE);
	if (ret < 0)
		return ret;
	priv->chip_type = ret;

	ret = devm_add_action_or_reset(dev, rtl8239_release_fw, priv);
	if (ret)
		return ret;

	/* Optional: without it the chip is used as it is until it shows up */
	if (rtl8239_get_fw(priv)) {
		ret = rtl8239_fw_load(priv);
		if (ret)
			return dev_err_probe(dev, ret, "cannot load the firmware\n");
	} else {
		dev_info(dev, "no firmware %s yet, looking again every %d s\n",
			 rtl8239_fw_name(priv), RTL8239_FW_CHECK_INTERVAL / HZ);
	}
	priv->next_fw_check = jiffies + RTL8239_FW_CHECK_INTERVAL;

	ret = rtl8239_read(priv, 0, RTL8239_REG_FW_STATUS);
	temp = rtl8239_read(priv, 0, RTL8239_REG_TEMP);
	dev_info(dev, "rev %lu, type %u, firmware status 0x%02x, %d C\n",
		 FIELD_GET(RTL8239_ID_REV, id), priv->chip_type, ret,
		 temp < 0 ? temp : temp * 652 / 1000 - 20);

	ret = rtl8239_enable_channels(priv);
	if (ret)
		return dev_err_probe(dev, ret, "cannot enable the ports\n");

	priv->pcdev.owner = THIS_MODULE;
	priv->pcdev.ops = &rtl8239_ops;
	priv->pcdev.types = ETHTOOL_PSE_C33;
	priv->pcdev.nr_lines = RTL8239_MAX_CHANS;

	ret = devm_pse_poll_helper(&priv->pcdev, &rtl8239_poll_desc);
	if (ret)
		return dev_err_probe(dev, ret, "cannot set up polling\n");

	ret = devm_pse_controller_register(dev, &priv->pcdev);
	if (ret)
		return dev_err_probe(dev, ret, "cannot register PSE controller\n");

	return 0;
}

static const struct i2c_device_id rtl8239_id[] = {
	{ "rtl8239" },
	{ }
};
MODULE_DEVICE_TABLE(i2c, rtl8239_id);

static const struct of_device_id rtl8239_of_match[] = {
	{ .compatible = "realtek,rtl8239" },
	{ }
};
MODULE_DEVICE_TABLE(of, rtl8239_of_match);

static struct i2c_driver rtl8239_driver = {
	.driver = {
		.name = "realtek-rtl8239",
		.of_match_table = rtl8239_of_match,
	},
	.probe = rtl8239_probe,
	.id_table = rtl8239_id,
};
module_i2c_driver(rtl8239_driver);

MODULE_FIRMWARE("realtek/rtl8239-bt.bin");
MODULE_FIRMWARE("realtek/rtl8239c-bt.bin");
MODULE_DESCRIPTION("Realtek RTL8239 PoE PSE controller");
MODULE_LICENSE("GPL");
