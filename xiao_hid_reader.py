#!/usr/bin/env python3
"""
Shared XIAO HID Reader Module

Provides HID device reading logic for Seeed XIAO SAMD21/RP2040.
Used by all XIAO sender implementations (TCP+TS, WebSocket, etc.)
"""

import os
import glob
import struct
import subprocess


class XiaoHIDReader:
    """Read key/paddle events from Seeed XIAO via raw hidraw interface"""
    
    # XIAO SAMD21 USB IDs (default)
    DEFAULT_VID = "2886"
    DEFAULT_PID = "802f"
    
    def __init__(self, device_path=None, vid=None, pid=None, debug=False):
        """
        Initialize HID reader
        
        Args:
            device_path: Explicit /dev/hidraw* path (auto-detect if None)
            vid: USB Vendor ID (hex string, e.g., "2886")
            pid: USB Product ID (hex string, e.g., "802f")
            debug: Enable debug output
        """
        self.device_path = device_path
        self.vid = vid or self.DEFAULT_VID
        self.pid = pid or self.DEFAULT_PID
        self.debug = debug
        self.device_fd = None
        
        # State tracking
        self.last_dit = False
        self.last_dah = False
        self.read_count = 0
    
    def get_device_ids(self, hidraw_path):
        """Get VID/PID from sysfs for a hidraw device"""
        try:
            hidraw_name = os.path.basename(hidraw_path)
            sysfs_base = f'/sys/class/hidraw/{hidraw_name}/device'
            
            # Try reading from uevent file
            uevent_path = f'{sysfs_base}/uevent'
            if os.path.exists(uevent_path):
                with open(uevent_path, 'r') as f:
                    content = f.read()
                    vid = None
                    pid = None
                    for line in content.split('\n'):
                        if line.startswith('HID_ID='):
                            # Format: HID_ID=0003:00002886:0000802F
                            parts = line.split('=')
                            if len(parts) == 2:
                                ids = parts[1].split(':')
                                if len(ids) == 3:
                                    vid = ids[1].lstrip('0').lower()
                                    pid = ids[2].lstrip('0').lower()
                                    return (vid, pid)
        except Exception:
            pass
        return (None, None)
    
    def find_device(self):
        """Find the hidraw device matching VID:PID"""
        for hidraw_path in glob.glob('/dev/hidraw*'):
            try:
                # Try sysfs first (more reliable)
                vid, pid = self.get_device_ids(hidraw_path)
                if vid and pid:
                    if vid == self.vid.lower() and pid == self.pid.lower():
                        return hidraw_path
                
                # Fallback to udevadm
                result = subprocess.run(
                    ['udevadm', 'info', hidraw_path],
                    capture_output=True,
                    text=True,
                    timeout=1
                )
                if result.returncode == 0:
                    output = result.stdout
                    if f'ID_VENDOR_ID={self.vid}' in output and f'ID_MODEL_ID={self.pid}' in output:
                        return hidraw_path
            except Exception:
                continue
        return None
    
    def connect(self):
        """
        Open HID device connection
        
        Returns:
            bool - True if successful, False otherwise
        """
        try:
            # Find the device (if not manually specified)
            if not self.device_path:
                self.device_path = self.find_device()
            
            if not self.device_path:
                print(f"Error: XIAO device not found (VID:PID {self.vid}:{self.pid})")
                print("\nAvailable hidraw devices:")
                for hidraw_path in glob.glob('/dev/hidraw*'):
                    vid, pid = self.get_device_ids(hidraw_path)
                    print(f"  {hidraw_path}")
                    if vid and pid:
                        print(f"    VID:PID = {vid}:{pid}")
                        if vid == self.vid.lower() and pid == self.pid.lower():
                            print(f"    *** This is the XIAO! Use: --device {hidraw_path}")
                    else:
                        # Try udevadm as fallback
                        try:
                            result = subprocess.run(
                                ['udevadm', 'info', hidraw_path],
                                capture_output=True,
                                text=True,
                                timeout=1
                            )
                            if result.returncode == 0:
                                for line in result.stdout.split('\n'):
                                    if 'ID_VENDOR_ID' in line or 'ID_MODEL_ID' in line or 'ID_SERIAL' in line:
                                        print(f"    {line.split('=')[1] if '=' in line else line}")
                        except Exception:
                            print("    (no device info available)")
                    print()
                print("\nTip: Use --device /dev/hidrawX to specify device manually")
                return False

            if self.debug:
                print(f"Found XIAO at: {self.device_path}")

            # Open device with raw file operations (non-blocking)
            self.device_fd = os.open(self.device_path, os.O_RDWR | os.O_NONBLOCK)
            
            print(f"Connected: XIAO SAMD21 (raw hidraw)")
            print(f"Device: {self.device_path}")
            print(f"VID:PID: {self.vid}:{self.pid}\n")
            return True

        except PermissionError:
            print(f"Error: Permission denied accessing {self.device_path}")
            print("\nTry one of these fixes:")
            print("1. Add udev rule (recommended):")
            print('   echo \'KERNEL=="hidraw*", ATTRS{idVendor}=="2886", ATTRS{idProduct}=="802f", MODE="0666", TAG+="uaccess"\' | sudo tee /etc/udev/rules.d/99-xiao.rules')
            print("   sudo udevadm control --reload-rules")
            print("   sudo udevadm trigger")
            print("\n2. Run with sudo (not recommended for permanent use):")
            print(f"   sudo python3 {' '.join(os.sys.argv)}")
            return False

        except Exception as e:
            print(f"Error connecting: {e}")
            return False
    
    def read_paddles(self):
        """
        Read paddle state from HID device
        
        Returns:
            tuple: (dit: bool, dah: bool) - Current paddle states
                   Returns last known state if no new data available
        """
        if self.device_fd is None:
            return (self.last_dit, self.last_dah)
        
        try:
            # Read up to 64 bytes (typical HID report size)
            data = os.read(self.device_fd, 64)
            if data and len(data) > 0:
                self.read_count += 1
                
                # Debug: Show reads when debugging
                if self.debug and self.read_count <= 10:
                    print(f"[HID READ #{self.read_count}] Received {len(data)} bytes: {data.hex()}")
                
                # HID report format: [report_id, button_data, ...]
                # XIAO firmware sends report ID 1
                report_id = data[0]
                
                if len(data) >= 2 and report_id in (0x01, 0x02):
                    buttons = data[1]  # Modifier byte (keyboard HID report format)
                elif len(data) >= 1:
                    # Fallback: assume no report ID
                    buttons = data[0]
                else:
                    # No data
                    return (self.last_dit, self.last_dah)
                
                # USB HID keyboard modifier bits (XIAO uses Keyboard library)
                # Bit 0 (0x01) = Left Ctrl  -> Dit paddle
                # Bit 4 (0x10) = Right Ctrl -> Dah paddle
                dit = bool(buttons & 0x01)  # Left Ctrl
                dah = bool(buttons & 0x10)  # Right Ctrl
                
                # Debug: Show button state changes
                if self.debug and (dit != self.last_dit or dah != self.last_dah):
                    print(f"[PADDLE] Dit={dit}, Dah={dah} (buttons=0x{buttons:02x})")
                
                self.last_dit = dit
                self.last_dah = dah
                
                return (dit, dah)
        
        except BlockingIOError:
            # No data available (non-blocking read)
            pass
        except Exception as e:
            if self.debug:
                print(f"[HID] Read error: {e}")
        
        return (self.last_dit, self.last_dah)
    
    def read_key(self):
        """
        Read straight key state (single paddle mode)
        
        Returns:
            bool: Key down state (True = pressed, False = released)
        """
        dit, dah = self.read_paddles()
        # For straight key, either paddle closure = key down
        return dit or dah
    
    def close(self):
        """Close HID device connection"""
        if self.device_fd is not None:
            try:
                os.close(self.device_fd)
            except Exception:
                pass
            self.device_fd = None
    
    def __enter__(self):
        """Context manager entry"""
        self.connect()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
