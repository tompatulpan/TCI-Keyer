#!/usr/bin/env python3
"""
Debug Script: Vail Adapter Keyboard Event Monitor

Tests if Vail adapter firmware is sending Ctrl/Ctrl-R keyboard events.
Can optionally configure the adapter via MIDI using config.yaml settings.

Usage: 
  python3 debug_vail_keyboard.py           # Just monitor
  python3 debug_vail_keyboard.py --config  # Configure via MIDI first

Expected behavior:
- Left paddle (dit) = Left Ctrl press/release
- Right paddle (dah) = Right Ctrl press/release

Press Esc to exit.
"""

import sys
import yaml
from pathlib import Path
from pynput import keyboard
from datetime import datetime

# Try to import MIDI support
try:
    import mido
    MIDI_AVAILABLE = True
except ImportError:
    MIDI_AVAILABLE = False


class VailMIDIConfigurator:
    """Configure Vail adapter via MIDI"""
    
    def __init__(self, config_path="config.yaml"):
        self.config = self._load_config(config_path)
        
    def _load_config(self, config_path):
        """Load configuration from YAML file"""
        config_file = Path(config_path)
        if not config_file.exists():
            print(f"⚠️  Config file not found: {config_path}")
            return None
        
        with open(config_file, 'r') as f:
            return yaml.safe_load(f)
    
    def find_vail_adapter(self):
        """Find Vail adapter MIDI device"""
        if not MIDI_AVAILABLE:
            return None
        
        try:
            output_names = mido.get_output_names()
            for name in output_names:
                # Vail adapter shows up as "XIAO SAMD21" in MIDI
                if "XIAO" in name.upper() or "SAMD21" in name.upper():
                    return name
            return None
        except Exception as e:
            print(f"⚠️  Error searching for MIDI device: {e}")
            return None
    
    def wpm_to_dit_ms(self, wpm):
        """Convert WPM to dit duration in milliseconds"""
        if wpm <= 0:
            return 60
        dit_ms = int((1200.0 / wpm) + 0.5)
        return max(10, min(127, dit_ms))
    
    def configure_adapter(self):
        """Send MIDI configuration to Vail adapter"""
        if not MIDI_AVAILABLE:
            print("❌ MIDI configuration not available (mido not installed)")
            print("   Install with: pip install mido python-rtmidi")
            return False
        
        if not self.config:
            print("❌ Configuration not loaded")
            return False
        
        # Get Vail adapter config
        vail_config = self.config.get('vail_adapter', {})
        if not vail_config.get('enabled', False):
            print("ℹ️  Vail adapter configuration disabled in config.yaml")
            return False
        
        # Find device
        device_name = self.find_vail_adapter()
        if not device_name:
            print("❌ Vail adapter MIDI device not found")
            print("   Check: ls -l /dev/snd/midi*")
            print("   Or run: python3 -c 'import mido; print(mido.get_output_names())'")
            return False
        
        print(f"✓ Found MIDI device: {device_name}")
        
        try:
            with mido.open_output(device_name) as port:
                # Get configuration values
                keyer_mode = vail_config.get('keyer_mode', 8)
                speed_wpm = vail_config.get('speed_wpm', 25)
                sidetone_note = vail_config.get('sidetone_note', 73)
                output_mode = vail_config.get('output_mode', 'keyboard')
                
                # Convert speed to dit duration
                dit_duration = self.wpm_to_dit_ms(speed_wpm)
                
                # Send MIDI commands
                print(f"\nSending MIDI configuration:")
                
                # 1. Set output mode (CC0)
                output_value = 127 if output_mode == 'keyboard' else 0
                msg = mido.Message('control_change', control=0, value=output_value)
                port.send(msg)
                print(f"  CC0 = {output_value} (output_mode: {output_mode})")
                
                # 2. Set keyer mode (Program Change)
                msg = mido.Message('program_change', program=keyer_mode)
                port.send(msg)
                keyer_names = {
                    0: "Passthrough", 1: "Straight", 2: "Bug", 3: "ElBug",
                    4: "SingleDot", 5: "Ultimatic", 6: "Plain",
                    7: "IambicA", 8: "IambicB", 9: "Keyahead"
                }
                print(f"  Program Change = {keyer_mode} ({keyer_names.get(keyer_mode, 'Unknown')})")
                
                # 3. Set speed (CC1)
                msg = mido.Message('control_change', control=1, value=dit_duration)
                port.send(msg)
                print(f"  CC1 = {dit_duration} ({speed_wpm} WPM)")
                
                # 4. Set sidetone (CC2)
                msg = mido.Message('control_change', control=2, value=sidetone_note)
                port.send(msg)
                print(f"  CC2 = {sidetone_note} (sidetone note)")
                
                print("\n✅ MIDI configuration sent successfully!")
                print("   Settings saved to adapter EEPROM")
                return True
                
        except Exception as e:
            print(f"❌ Error sending MIDI configuration: {e}")
            return False


class VailKeyboardMonitor:
    """Monitor keyboard events from Vail adapter"""
    
    def __init__(self):
        self.dit_pressed = False
        self.dah_pressed = False
        self.event_count = 0
        self.start_time = datetime.now()
        
    def format_timestamp(self):
        """Get timestamp since start"""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        return f"{elapsed:7.3f}s"
    
    def on_press(self, key):
        """Handle key press"""
        timestamp = self.format_timestamp()
        
        try:
            if key == keyboard.Key.ctrl_l:
                if not self.dit_pressed:
                    self.dit_pressed = True
                    self.event_count += 1
                    print(f"[{timestamp}] ✓ DIT DOWN  (Left Ctrl)  ●")
                    
            elif key == keyboard.Key.ctrl_r:
                if not self.dah_pressed:
                    self.dah_pressed = True
                    self.event_count += 1
                    print(f"[{timestamp}] ✓ DAH DOWN  (Right Ctrl) ●●●")
                    
            elif key == keyboard.Key.esc:
                print(f"\n[{timestamp}] ESC pressed - Exiting...")
                return False  # Stop listener
                
        except AttributeError:
            # Not a special key
            pass
        
        return True
    
    def on_release(self, key):
        """Handle key release"""
        timestamp = self.format_timestamp()
        
        try:
            if key == keyboard.Key.ctrl_l:
                if self.dit_pressed:
                    self.dit_pressed = False
                    self.event_count += 1
                    print(f"[{timestamp}] ✓ DIT UP    (Left Ctrl)  ○")
                    
            elif key == keyboard.Key.ctrl_r:
                if self.dah_pressed:
                    self.dah_pressed = False
                    self.event_count += 1
                    print(f"[{timestamp}] ✓ DAH UP    (Right Ctrl) ○○○")
                    
        except AttributeError:
            pass
        
        return True
    
    def print_header(self):
        """Print startup information"""
        print("="*70)
        print("VAIL ADAPTER KEYBOARD EVENT MONITOR")
        print("="*70)
        print()
        print("This script monitors for Ctrl key events from Vail adapter firmware.")
        print()
        print("Expected events:")
        print("  Left paddle (dit)  → Left Ctrl  (Key.ctrl_l)")
        print("  Right paddle (dah) → Right Ctrl (Key.ctrl_r)")
        print()
        print("Instructions:")
        print("  1. Make sure your Vail adapter is connected via USB")
        print("  2. Touch the LEFT paddle → should see 'DIT DOWN' and 'DIT UP'")
        print("  3. Touch the RIGHT paddle → should see 'DAH DOWN' and 'DAH UP'")
        print("  4. Press ESC to exit")
        print()
        print("="*70)
        print("Listening for keyboard events... (Press ESC to exit)")
        print("="*70)
        print()
    
    def print_summary(self):
        """Print summary statistics"""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        print()
        print("="*70)
        print("SESSION SUMMARY")
        print("="*70)
        print(f"Total events detected: {self.event_count}")
        print(f"Session duration: {elapsed:.1f} seconds")
        print()
        
        if self.event_count == 0:
            print("❌ NO EVENTS DETECTED")
            print()
            print("Troubleshooting:")
            print("  1. Check if Vail adapter is connected: lsusb | grep 2886")
            print("  2. Verify firmware is flashed: ls -l /dev/hidraw*")
            print("  3. Test with: cat /dev/input/by-id/*SAMD21* (requires sudo)")
            print("  4. Check if mido is available: pip list | grep mido")
            print()
            print("If still not working, the firmware may not be Vail adapter v4.4+")
        else:
            print("✅ VAIL ADAPTER IS WORKING!")
            print()
            print("The firmware is correctly sending keyboard events.")
            print("You can now use main.py with vail_adapter.enabled=true")
        
        print("="*70)
    
    def run(self):
        """Start monitoring"""
        self.print_header()
        
        try:
            with keyboard.Listener(
                on_press=self.on_press,
                on_release=self.on_release
            ) as listener:
                listener.join()
        except KeyboardInterrupt:
            print("\n\nInterrupted by user (Ctrl+C)")
        finally:
            self.print_summary()


def main():
    """Main entry point"""
    # Check for --config argument
    configure_midi = '--config' in sys.argv or '-c' in sys.argv
    
    # Show MIDI status
    if MIDI_AVAILABLE:
        print(f"✓ MIDI support available (mido installed)\n")
    else:
        print(f"⚠️  MIDI support not available (install with: pip install mido python-rtmidi)\n")
    
    # Configure via MIDI if requested
    if configure_midi:
        print("="*70)
        print("STEP 1: CONFIGURE VAIL ADAPTER VIA MIDI")
        print("="*70)
        print()
        
        configurator = VailMIDIConfigurator()
        success = configurator.configure_adapter()
        
        if success:
            print("\nWaiting 2 seconds for adapter to apply settings...")
            import time
            time.sleep(2)
        
        print()
        print("="*70)
        print("STEP 2: MONITOR KEYBOARD EVENTS")
        print("="*70)
        print()
    
    # Check pynput backend
    try:
        import pynput.keyboard._xorg
        backend = "X11/Xlib (XWayland)"
    except ImportError:
        try:
            import pynput.keyboard._uinput
            backend = "evdev/uinput"
        except ImportError:
            backend = "unknown"
    
    print(f"pynput backend: {backend}\n")
    
    # Start monitoring
    monitor = VailKeyboardMonitor()
    monitor.run()


if __name__ == "__main__":
    main()
