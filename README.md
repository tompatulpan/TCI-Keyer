# TCI CW Controller

Python application for controlling SunSDR radios via TCI (Transceiver Command Interface) protocol with support for:

- **F1-F12 keyboard macros** - Send predefined CW text messages
- **USB paddle input** - Manual CW keying via XIAO SAMD21 microcontroller
- **Local sidetone** - Instant audio feedback for paddle operation (<20ms latency)
- **Configurable** - YAML-based configuration for callsign, messages, and settings

**Status:** 🚧 In development - Features and structure subject to change

## Features

### F-Key CW Macros
- Press F1-F12 to send pre-configured CW messages
- Automatic callsign substitution using `{callsign}` placeholder
- Text-to-morse conversion handled by ExpertSDR3/TCI
- Special characters supported: `>` (faster), `<` (slower), `|SK|` (prosigns)

### USB Paddle Manual Keying
- Connect CW paddle via XIAO SAMD21 USB HID interface
- Accurate timing measurement (<1ms precision)
- Local sidetone for instant feedback (no network latency)
- Straight key mode (paddle states sent to TCI)
- Optional iambic keyer support (if TCI doesn't handle it)

### TCI Protocol
- WebSocket connection to ExpertSDR3 TCI server
- Configurable host and port
- Auto-reconnection on disconnect
- Supports multiple transceivers

## Hardware Requirements

### USB Paddle Input (Optional)
- **Seeed XIAO SAMD21** microcontroller (~$5-8 USD)
- CW paddle (iambic or straight key)
- USB-C cable

**Paddle connections:**
- D2 (PA08) → Dit paddle
- D1 (PA04) → Dah paddle
- GND → Common ground

See [USB_HID_copy/HARDWARE.md](USB_HID_copy/HARDWARE.md) for detailed wiring.

## Software Requirements

- Python 3.8 or higher
- Linux (Ubuntu, or similar) - for USB HID support
- ExpertSDR3 with TCI protocol enabled
- Windows should work, NOT tested yet.

## Installation

### 1. Install Dependencies

```bash
# Install Python packages
pip install -r requirements.txt
```

Dependencies:
- `websockets` - TCI WebSocket client
- `pynput` - Keyboard input for F-keys
- `pyaudio` - Local sidetone audio
- `PyYAML` - Configuration file parsing

### 2. Flash XIAO Firmware (USB Paddle Only)

If using USB paddle input:

1. Install Arduino IDE 2.x
2. Add Seeeduino SAMD boards:
   - Tools → Board Manager → Search "Seeed SAMD Boards"
   - Install "Seeed SAMD Boards"
3. Select board: Tools → Board → Seeed SAMD → "Seeed XIAO M0"
4. Open firmware: `USB_HID_copy/xiao_samd21_hid_key/xiao_samd21_hid_key.ino`
5. Click Upload
6. Verify: `lsusb -d 2886:802f` should show "Seeed XIAO SAMD21"

### 3. Setup USB Permissions (USB Paddle Only)

```bash
# Run installer script (works for Fedora & Ubuntu)
./install_udev.sh

# Verify device access
ls -l /dev/hidraw*
# Should show mode 0666 or owned by your user
```

Manual setup:
```bash
# Create udev rule
echo 'KERNEL=="hidraw*", ATTRS{idVendor}=="2886", ATTRS{idProduct}=="802f", MODE="0666", TAG+="uaccess"' | \
  sudo tee /etc/udev/rules.d/99-xiao-samd21.rules

# Reload rules
sudo udevadm control --reload-rules
sudo udevadm trigger

# Replug USB device
```

### 4. Configure TCI Controller

Edit [config.yaml](config.yaml):

```yaml
# Radio connection
tci:
  host: "localhost"      # TCI server IP
  port: 40001            # TCI port (check ExpertSDR3 settings)
  
# Operator info
operator:
  callsign: "W1AW"       # Your callsign

# CW settings
cw:
  speed_wpm: 25          # CW speed

# Sidetone (for USB paddle)
sidetone:
  enabled: true
  frequency: 600         # Hz
  volume: 0.5            # 0.0-1.0

# Function key macros
function_keys:
  F1: "CQ CQ CQ DE {callsign} {callsign} K"
  F2: "DE {callsign} K"
  F3: "{callsign} 599"
  # ... etc
```

## Usage

### Start the Controller

```bash
python3 main.py
```

Expected output:
```
15:23:45 [INFO] TCICWController: Connecting to TCI server localhost:40001
15:23:45 [INFO] TCIClient: Connected to TCI server
15:23:46 [INFO] TCIClient: TCI server is READY
15:23:46 [INFO] KeyboardHandler: Starting F-key listener (F1-F12 for CW macros)
15:23:46 [INFO] USBPaddleHandler: USB paddle connected
15:23:46 [INFO] TCICWController: Initializing sidetone: 600 Hz

============================================================
TCI CW CONTROLLER READY
============================================================
F1-F12: Send CW macros
USB Paddle: Manual keying
Ctrl+C: Quit
============================================================
```

### Send CW Macros

Press function keys:
- **F1** - CQ message
- **F2** - Short reply
- **F3** - Signal report
- (etc., as configured)

The configured message will be sent to TCI with `{callsign}` substituted.

### Manual Paddle Keying

If USB paddle is connected:
- Press dit/dah paddles
- Local sidetone provides instant feedback
- Key state sent to radio via TCI KEYER command

### Stop the Controller

Press **Ctrl+C** for graceful shutdown.

## Configuration Reference

### TCI Settings

```yaml
tci:
  host: "localhost"        # TCI server hostname/IP
  port: 40001              # TCI server port
  trx_number: 0            # Transceiver number (usually 0)
  auto_reconnect: true     # Reconnect on disconnect
  reconnect_delay: 3.0     # Seconds between reconnect attempts
```

**Finding TCI Port:**
- Check ExpertSDR3 settings/documentation
- Common ports: 40001, 50001
- Try: `netstat -an | grep LISTEN` to see open ports

### CW Settings

```yaml
cw:
  default_mode: "CW"       # CW, CWL, or CWU
  speed_wpm: 25            # Words per minute
  keyer_mode: "straight"   # straight, iambic-a, iambic-b
```

**Keyer Mode:**
- `straight` - Send raw paddle states to TCI (recommended - test first)
- `iambic-a` - Client-side iambic Mode A (if TCI doesn't handle it)
- `iambic-b` - Client-side iambic Mode B with paddle memory

### USB HID Settings

```yaml
usb_hid:
  enabled: true            # Enable USB paddle
  device_path: null        # Auto-detect, or specify: /dev/hidraw0
  vendor_id: "2886"        # XIAO SAMD21 VID
  product_id: "802f"       # XIAO SAMD21 PID
  debug: false             # Enable HID debug output
```

### Function Key Macros

```yaml
function_keys:
  F1: "CQ CQ CQ DE {callsign} {callsign} K"
  F2: "DE {callsign} K"
  F3: "{callsign} 599"
  F4: "TU 73 DE {callsign}"
  F5: "QRZ? DE {callsign} K"
  F6: "PSE QRS"
  F7: "QRL?"
  F8: "TEST DE {callsign}"
  F9: ""  # Not assigned
```

**Special Characters:**
- `{callsign}` - Replaced with your callsign
- `>` - Increase speed by 5 WPM
- `<` - Decrease speed by 5 WPM
- `|SK|` - Send prosign (combine letters)
- `|AR|`, `|BT|`, etc.

**Reserved Characters** (automatically escaped):
- `:` becomes `^`
- `,` becomes `~`
- `;` becomes `*`

## Troubleshooting

### TCI Connection Failed

```
[ERROR] TCIClient: Connection timeout after 5s
```

**Solutions:**
1. Check ExpertSDR3 is running
2. Verify TCI is enabled in ExpertSDR3 settings
3. Check port number in config.yaml
4. Try: `telnet localhost 40001` to test connection
5. Check firewall settings

### USB Paddle Not Found

```
[WARNING] USB paddle not found - manual keying disabled
```

**Solutions:**
1. Check XIAO is connected: `lsusb -d 2886:802f`
2. Check firmware is flashed (LED should blink on startup)
3. Run udev installer: `./install_udev.sh`
4. Check permissions: `ls -l /dev/hidraw*`
5. Try manual device path in config: `device_path: /dev/hidraw0`

### Sidetone Failed

```
[WARNING] Failed to initialize sidetone: [Errno -9996] Invalid output device
```

**Solutions:**
1. Check audio output device: `aplay -l`
2. Install PyAudio: `pip install pyaudio`
3. Install portaudio: `sudo dnf install portaudio` (Fedora) or `sudo apt install portaudio19-dev` (Ubuntu)
4. Disable sidetone in config if not needed

### Permission Denied

```
[ERROR] Error: Permission denied accessing /dev/hidraw0
```

**Solutions:**
1. Run udev installer: `./install_udev.sh`
2. Replug USB device after installing rules
3. Check user is in correct group: `groups`
4. Temporary fix: `sudo python3 main.py` (not recommended)

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    main.py                          │
│              (TCICWController)                      │
│                                                     │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────┐ │
│  │ TCI Client  │  │  F-Key       │  │ USB       │ │
│  │ (WebSocket) │  │  Handler     │  │ Paddle    │ │
│  └──────┬──────┘  └──────┬───────┘  └─────┬─────┘ │
│         │                │                 │       │
└─────────┼────────────────┼─────────────────┼───────┘
          │                │                 │
          ↓                ↓                 ↓
    ExpertSDR3       Keyboard          XIAO SAMD21
    TCI Server       (pynput)          USB HID
          │                                  │
          │                                  ↓
          │                           ┌─────────────┐
          │                           │  Sidetone   │
          │                           │  Generator  │
          └───────────────────────────┤  (PyAudio)  │
                                      └─────────────┘
```

## Files

- `main.py` - Main application entry point
- `config.yaml` - Configuration file
- `tci_client.py` - TCI WebSocket protocol client
- `keyboard_handler.py` - F-key macro handler
- `usb_paddle_handler.py` - USB paddle input with timing
- `sidetone_generator.py` - Local audio sidetone
- `xiao_hid_reader.py` - USB HID device reader (from USB_HID_copy)
- `install_udev.sh` - udev rules installer
- `requirements.txt` - Python dependencies

## License

MIT License

## Credits

- USB HID implementation inspired by the Vail-CW adapter project
- TCI protocol specification from ExpertSDR3 - https://github.com/ExpertSDR3/TCI

## Support

For issues:
1. Check troubleshooting section above
2. Verify hardware connections
3. Test components independently
4. Check logs for error messages

## Development

### Testing Individual Components

**Test USB HID reader:**
```bash
python3 -c "
from xiao_hid_reader import XiaoHIDReader
import time
r = XiaoHIDReader(debug=True)
if r.connect():
    print('Press paddles (Ctrl+C to quit)...')
    while True:
        dit, dah = r.read_paddles()
        if dit or dah:
            print(f'Dit={dit}, Dah={dah}')
        time.sleep(0.01)
"
```

**Test sidetone:**
```bash
python3 -c "
from sidetone_generator import SidetoneGenerator
import time
s = SidetoneGenerator(frequency=600)
print('Testing sidetone (5 seconds)...')
s.set_key(True)
time.sleep(5)
s.set_key(False)
s.close()
print('Done')
"
```

**Test TCI connection:**
```bash
python3 -c "
import asyncio
from tci_client import TCIClient

async def test():
    client = TCIClient('localhost', 40001)
    if await client.connect():
        print('Connected!')
        await client.send_cw_macros('TEST DE W1AW')
        await asyncio.sleep(2)
        await client.disconnect()
    else:
        print('Connection failed')

asyncio.run(test())
"
```

## ToDo / Feature Wishlist

- [ ] **Send as you write**: Option to transmit CW in real-time as you type, not just via macros or paddle.
- [ ] **Quick "Repeat last sent" button**: Instantly resend the last transmitted message.
- [ ] **Graphical User Interface (GUI)**: User-friendly interface for configuration, macro editing, and live status.
- [ ] **Windows support**: Port application for Windows, including USB HID and audio compatibility.
- [ ] Macro editor: Edit F-key macros from the GUI without editing YAML.
- [ ] Contest mode: Add QSO numbering, serials, and contest logging features.
- [ ] Network paddle: Support remote paddle input via UDP or TCP.
- [ ] Hamlib/CAT fallback: Alternative to TCI for radios without TCI support.
- [ ] Advanced error handling: More robust reconnect and diagnostics.
- [ ] Customizable sidetone: Per-operator sidetone profiles and advanced audio settings.
