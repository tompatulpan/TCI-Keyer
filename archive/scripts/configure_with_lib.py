#!/usr/bin/env python3
"""Configure Vail adapter using vail-adapter-lib"""

import sys
sys.path.insert(0, '../vail-adapter-lib')

import yaml

# Import from the library
from vail_adapter_lib.midi_config import VailAdapterConfig

# Load config
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

vail_config = config.get('vail_adapter', {})
speed_wpm = vail_config.get('speed_wpm', 25)
keyer_mode = vail_config.get('keyer_mode', 8)
sidetone_note = vail_config.get('sidetone_note', 73)
output_mode = vail_config.get('output_mode', 'keyboard')
keyboard_mode = (output_mode == 'keyboard')

print("="*70)
print("CONFIGURE VAIL ADAPTER USING vail-adapter-lib")
print("="*70)
print()
print(f"Configuration from config.yaml:")
print(f"  Speed: {speed_wpm} WPM")
print(f"  Keyer mode: {keyer_mode} (Iambic B)")
print(f"  Output mode: {output_mode}")
print(f"  Sidetone note: {sidetone_note}")
print()

try:
    # Create MIDI configurator (VailAdapterConfig class)
    configurator = VailAdapterConfig()
    
    # Configure the adapter
    print("Sending configuration via vail-adapter-lib...")
    success = configurator.configure_adapter(
        keyer_mode=keyer_mode,
        speed_wpm=speed_wpm,
        sidetone_note=sidetone_note,
        keyboard_mode=keyboard_mode
    )
    
    if success:
        print()
        print("="*70)
        print("✅ CONFIGURATION SUCCESSFUL!")
        print("="*70)
        print()
        print("The vail-adapter-lib correctly uses CC1 = dit_duration / 2")
        print()
        print("Wait 2 seconds for adapter to apply settings...")
        import time
        time.sleep(2)
        print()
        print("Now test: python3 debug_vail_timing.py")
        print("You should see '[25 WPM]' in the output")
    else:
        print()
        print("="*70)
        print("❌ CONFIGURATION FAILED")
        print("="*70)
        print()
        print("Check that:")
        print("  1. XIAO is connected (lsusb | grep 2886)")
        print("  2. MIDI device is available")
        print("  3. mido is installed (pip install mido python-rtmidi)")
        
except Exception as e:
    print()
    print("="*70)
    print("❌ ERROR")
    print("="*70)
    print()
    print(f"Error: {e}")
    print()
    import traceback
    traceback.print_exc()
    print()
    print("Make sure vail-adapter-lib is properly set up")
