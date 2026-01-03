#!/usr/bin/env python3
"""Quick MIDI configuration for Vail adapter - CORRECTED VERSION"""

import yaml
import time
import mido

# Load config
with open('config.yaml', 'r') as f:
    config = yaml.safe_load(f)

vail_config = config.get('vail_adapter', {})
speed_wpm = vail_config.get('speed_wpm', 25)
keyer_mode = vail_config.get('keyer_mode', 8)
sidetone_note = vail_config.get('sidetone_note', 73)

# Calculate CC1 value
# IMPORTANT: Vail firmware expects CC1 = (dit_duration_ms / 2)
dit_duration_ms = 1200.0 / speed_wpm
cc1_value = int((dit_duration_ms / 2) + 0.5)
cc1_value = max(0, min(127, cc1_value))

print(f"Configuring Vail adapter (CORRECTED):")
print(f"  Speed: {speed_wpm} WPM")
print(f"  Dit: {dit_duration_ms:.0f} ms")
print(f"  CC1: {cc1_value} (dit/2)")
print()

# Find device
device_name = None
for name in mido.get_output_names():
    if "XIAO" in name.upper() or "Seeed" in name:
        device_name = name
        break

if not device_name:
    print("❌ XIAO not found!")
    exit(1)

print(f"✓ Found: {device_name}\n")

with mido.open_output(device_name) as port:
    port.send(mido.Message('control_change', control=0, value=127))
    print("  ✓ CC0 = 127")
    time.sleep(0.05)
    
    port.send(mido.Message('program_change', program=keyer_mode))
    print(f"  ✓ PC = {keyer_mode}")
    time.sleep(0.05)
    
    port.send(mido.Message('control_change', control=1, value=cc1_value))
    print(f"  ✓ CC1 = {cc1_value} ← CORRECTED (was 48)")
    time.sleep(0.05)
    
    port.send(mido.Message('control_change', control=2, value=sidetone_note))
    print(f"  ✓ CC2 = {sidetone_note}")

print("\n✅ Done! Wait 2 sec...\n")
time.sleep(2)
print("Test: python3 debug_vail_timing.py")
