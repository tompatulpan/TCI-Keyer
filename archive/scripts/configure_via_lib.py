#!/usr/bin/env python3
"""Use vail-adapter-lib to configure via MIDI"""

import sys
sys.path.insert(0, '../vail-adapter-lib')

import yaml

# Import library
from vail_adapter_lib.midi_config import VailMIDIConfig

# Load config
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

vail_config = config.get('vail_adapter', {})
speed_wpm = vail_config.get('speed_wpm', 25)
keyer_mode = vail_config.get('keyer_mode', 8)
sidetone_note = vail_config.get('sidetone_note', 73)
output_mode = vail_config.get('output_mode', 'keyboard')

print(f"Using vail-adapter-lib to configure:")
print(f"  Speed: {speed_wpm} WPM")
print(f"  Keyer mode: {keyer_mode} (Iambic B)")
print(f"  Output mode: {output_mode}")
print()

try:
    # Create MIDI config object
    midi_config = VailMIDIConfig()
    
    # Configure adapter
    success = midi_config.configure_adapter(
        keyer_mode=keyer_mode,
        speed_wpm=speed_wpm,
        sidetone_note=sidetone_note,
        keyboard_mode=(output_mode == 'keyboard')
    )
    
    if success:
        print("\n✅ Configuration successful!")
        print("\nUnplug and replug the XIAO to reload from EEPROM")
        print("Then test: python3 debug_vail_timing.py")
    else:
        print("\n❌ Configuration failed")
        
except Exception as e:
    print(f"\n❌ Error: {e}")
    import traceback
    traceback.print_exc()
