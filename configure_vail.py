#!/usr/bin/env python3
"""
Configure Vail Adapter Firmware via MIDI

Simple standalone script to configure Vail adapter settings from config.yaml.
Settings are stored in EEPROM and persist across reboots.

Usage:
    python3 configure_vail.py
    
Requirements:
    - mido, python-rtmidi (pip install mido python-rtmidi)
    - Vail adapter firmware flashed to XIAO SAMD21
    - config.yaml with vail_adapter section
"""

import yaml
import time
import sys

try:
    import mido
except ImportError:
    print("Error: mido library not installed")
    print("Install with: pip install mido python-rtmidi")
    sys.exit(1)


def find_vail_adapter():
    """Find Vail adapter MIDI device"""
    output_names = mido.get_output_names()
    
    # Search for common Vail/XIAO device names
    patterns = ["Vail", "vail", "XIAO", "xiao", "Seeed"]
    for name in output_names:
        for pattern in patterns:
            if pattern in name:
                return name
    
    print(f"Available MIDI devices: {output_names}")
    return None


def configure_adapter(device_name, keyer_mode, speed_wpm, sidetone_note, keyboard_mode=True):
    """
    Send MIDI configuration to Vail adapter
    
    Args:
        device_name: MIDI device name
        keyer_mode: 0-9 (0=passthrough, 1=straight, 8=iambic-b, etc.)
        speed_wpm: Words per minute (5-55)
        sidetone_note: MIDI note (60-96, ~261-1568 Hz)
        keyboard_mode: True for keyboard output, False for MIDI
    """
    # Calculate dit duration for CC1
    dit_duration_ms = 1200.0 / speed_wpm
    cc1_value = int((dit_duration_ms / 2) + 0.5)
    cc1_value = max(0, min(127, cc1_value))
    
    print(f"\nConfiguring Vail adapter: {device_name}")
    print(f"  Keyer mode: {keyer_mode}")
    print(f"  Speed: {speed_wpm} WPM (dit={dit_duration_ms:.0f}ms, CC1={cc1_value})")
    print(f"  Sidetone: MIDI note {sidetone_note}")
    print(f"  Output: {'Keyboard' if keyboard_mode else 'MIDI'}")
    print()
    
    try:
        with mido.open_output(device_name) as port:
            # CC0: Output mode (127=keyboard, 0=MIDI)
            port.send(mido.Message('control_change', control=0, value=127 if keyboard_mode else 0))
            print("  ✓ CC0: Output mode set")
            time.sleep(0.05)
            
            # Program Change: Keyer mode
            port.send(mido.Message('program_change', program=keyer_mode))
            print(f"  ✓ PC: Keyer mode {keyer_mode}")
            time.sleep(0.05)
            
            # CC1: Speed (dit duration / 2)
            port.send(mido.Message('control_change', control=1, value=cc1_value))
            print(f"  ✓ CC1: Speed set")
            time.sleep(0.05)
            
            # CC2: Sidetone note
            port.send(mido.Message('control_change', control=2, value=sidetone_note))
            print(f"  ✓ CC2: Sidetone set")
            time.sleep(0.05)
            
            print("\n✓ Configuration complete! Settings saved to EEPROM.")
            return True
            
    except Exception as e:
        print(f"\n❌ Error: {e}")
        return False


def main():
    # Load configuration
    try:
        with open('config.yaml', 'r') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print("Error: config.yaml not found")
        sys.exit(1)
    
    # Get Vail adapter settings
    vail_config = config.get('vail_adapter', {})
    if not vail_config.get('enabled', False):
        print("Warning: vail_adapter.enabled = false in config.yaml")
        print("Set to true if using Vail firmware")
        print()
    
    keyer_mode = vail_config.get('keyer_mode', 8)
    speed_wpm = vail_config.get('speed_wpm', 25)
    sidetone_note = vail_config.get('sidetone_note', 73)
    output_mode = vail_config.get('output_mode', 'keyboard')
    keyboard_mode = (output_mode == 'keyboard')
    
    # Find device
    device_name = find_vail_adapter()
    if not device_name:
        print("❌ Vail adapter not found!")
        print("Make sure:")
        print("  1. XIAO SAMD21 is connected via USB")
        print("  2. Vail firmware is flashed (not custom firmware)")
        print("  3. MIDI device shows up in system")
        sys.exit(1)
    
    # Configure
    success = configure_adapter(device_name, keyer_mode, speed_wpm, 
                                sidetone_note, keyboard_mode)
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
