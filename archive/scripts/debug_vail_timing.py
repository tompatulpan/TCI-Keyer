#!/usr/bin/env python3
"""
Debug Script: Vail Adapter Timing & Morse Decoder

Analyzes paddle timing and decodes Morse code to verify configuration.
Shows actual WPM and decoded characters.

Usage: python3 debug_vail_timing.py [--config]
"""

import sys
import yaml
import time
from pathlib import Path
from pynput import keyboard
from datetime import datetime
from collections import deque

# Try to import MIDI support
try:
    import mido
    MIDI_AVAILABLE = True
except ImportError:
    MIDI_AVAILABLE = False


class MorseDecoder:
    """Decode Morse code from timing"""
    
    # International Morse Code table
    MORSE_CODE = {
        '.-': 'A', '-...': 'B', '-.-.': 'C', '-..': 'D', '.': 'E',
        '..-.': 'F', '--.': 'G', '....': 'H', '..': 'I', '.---': 'J',
        '-.-': 'K', '.-..': 'L', '--': 'M', '-.': 'N', '---': 'O',
        '.--.': 'P', '--.-': 'Q', '.-.': 'R', '...': 'S', '-': 'T',
        '..-': 'U', '...-': 'V', '.--': 'W', '-..-': 'X', '-.--': 'Y',
        '--..': 'Z',
        '-----': '0', '.----': '1', '..---': '2', '...--': '3', '....-': '4',
        '.....': '5', '-....': '6', '--...': '7', '---..': '8', '----.': '9',
        '.-.-.-': '.', '--..--': ',', '..--..': '?', '-..-.': '/', 
        '-....-': '-', '.--.-.': '@', '-.-.--': '!', '..--.-': '_'
    }
    
    def __init__(self, tolerance=0.5):
        """
        Initialize decoder
        
        Args:
            tolerance: Timing tolerance (0.5 = 50%)
        """
        self.tolerance = tolerance
        self.current_char = []
        self.decoded_text = []
        self.dit_duration = None
        self.last_up_time = None
        
        # Statistics
        self.element_durations = deque(maxlen=50)  # Last 50 elements
        self.space_durations = deque(maxlen=50)
        
    def update_dit_duration(self, duration):
        """Update estimated dit duration from element"""
        self.element_durations.append(duration)
        
        # Calculate average of shortest elements (likely dits)
        if len(self.element_durations) >= 5:
            sorted_durations = sorted(self.element_durations)
            # Average of shortest 30%
            dit_samples = sorted_durations[:max(1, len(sorted_durations) // 3)]
            self.dit_duration = sum(dit_samples) / len(dit_samples)
    
    def classify_element(self, duration):
        """
        Classify element as dit or dah
        
        Returns:
            '.' for dit, '-' for dah, None if uncertain
        """
        if self.dit_duration is None:
            # First element, assume it's a dit
            self.dit_duration = duration
            return '.'
        
        # Ratio to dit duration
        ratio = duration / self.dit_duration
        
        if ratio < (1 + self.tolerance):
            return '.'  # Dit
        elif ratio > (2 - self.tolerance) and ratio < (4 + self.tolerance):
            return '-'  # Dah
        else:
            return None  # Unknown
    
    def add_element(self, duration):
        """Add a dit or dah element"""
        self.update_dit_duration(duration)
        element = self.classify_element(duration)
        
        if element:
            self.current_char.append(element)
            return element
        return None
    
    def add_space(self, duration):
        """Add space between elements or characters"""
        if self.dit_duration is None:
            return None
        
        self.space_durations.append(duration)
        
        # Classify space
        ratio = duration / self.dit_duration
        
        if ratio > 5:  # Word space (7 dits typically)
            # End character and add word space
            char = self.end_character()
            self.decoded_text.append(' ')
            return 'WORD_SPACE'
        elif ratio > 2:  # Character space (3 dits typically)
            # End character
            self.end_character()
            return 'CHAR_SPACE'
        else:
            # Still within character
            return 'ELEMENT_SPACE'
    
    def end_character(self):
        """End current character and decode"""
        if not self.current_char:
            return None
        
        morse = ''.join(self.current_char)
        char = self.MORSE_CODE.get(morse, f'[{morse}]')
        self.decoded_text.append(char)
        self.current_char = []
        return char
    
    def get_decoded_text(self):
        """Get decoded text"""
        return ''.join(self.decoded_text)
    
    def get_wpm(self):
        """Calculate WPM from dit duration"""
        if self.dit_duration is None:
            return None
        # Standard: 1 dit = 1.2 / WPM seconds
        # So WPM = 1.2 / dit_duration
        return 1.2 / self.dit_duration
    
    def get_stats(self):
        """Get timing statistics"""
        if self.dit_duration is None:
            return None
        
        return {
            'dit_ms': int(self.dit_duration * 1000),
            'dah_ms': int(self.dit_duration * 3 * 1000),
            'wpm': int(self.get_wpm()),
            'elements': len(self.element_durations),
            'chars': len([c for c in self.decoded_text if c not in [' ', '\n']])
        }


class VailTimingAnalyzer:
    """Analyze Vail adapter timing and decode Morse"""
    
    def __init__(self):
        self.key_down = False
        self.key_down_time = None
        self.key_up_time = None
        self.decoder = MorseDecoder()
        self.event_count = 0
        self.start_time = datetime.now()
        
    def on_press(self, key):
        """Handle key press"""
        try:
            if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                if not self.key_down:
                    self.key_down = True
                    self.key_down_time = time.time()
                    self.event_count += 1
                    
                    # Process space if there was a key-up
                    if self.key_up_time:
                        space_duration = self.key_down_time - self.key_up_time
                        space_type = self.decoder.add_space(space_duration)
                        
                        # Show space classification
                        if space_type == 'CHAR_SPACE':
                            stats = self.decoder.get_stats()
                            if stats:
                                print(f"  [{stats['wpm']:2d} WPM] ", end='', flush=True)
                        elif space_type == 'WORD_SPACE':
                            print(" / ", end='', flush=True)
                    
                    paddle = "DIT" if key == keyboard.Key.ctrl_l else "DAH"
                    print(f"{paddle[0]}", end='', flush=True)
                    
            elif key == keyboard.Key.esc:
                return False
                
        except AttributeError:
            pass
        
        return True
    
    def on_release(self, key):
        """Handle key release"""
        try:
            if key == keyboard.Key.ctrl_l or key == keyboard.Key.ctrl_r:
                if self.key_down:
                    self.key_down = False
                    self.key_up_time = time.time()
                    self.event_count += 1
                    
                    # Calculate element duration
                    if self.key_down_time:
                        duration = self.key_up_time - self.key_down_time
                        element = self.decoder.add_element(duration)
                        
        except AttributeError:
            pass
        
        return True
    
    def print_header(self):
        """Print startup information"""
        print("="*70)
        print("VAIL ADAPTER TIMING ANALYZER & MORSE DECODER")
        print("="*70)
        print()
        print("This script analyzes paddle timing and decodes Morse code.")
        print()
        print("Live display format:")
        print("  D = Dit (left paddle)")
        print("  A = Dah (right paddle)")
        print("  [XX WPM] = Current speed")
        print("  / = Word space")
        print()
        print("Instructions:")
        print("  1. Send some Morse code with your paddle")
        print("  2. Watch the live decode and WPM calculation")
        print("  3. Press ESC when done to see summary")
        print()
        print("Example test: Send 'CQ' (dah-dit-dah-dit  dah-dah-dit-dah)")
        print()
        print("="*70)
        print("Ready - Start keying! (Press ESC to exit)")
        print("="*70)
        print()
    
    def print_summary(self):
        """Print summary statistics"""
        # End any pending character
        self.decoder.end_character()
        
        elapsed = (datetime.now() - self.start_time).total_seconds()
        stats = self.decoder.get_stats()
        decoded = self.decoder.get_decoded_text()
        
        print("\n")
        print("="*70)
        print("SESSION SUMMARY")
        print("="*70)
        
        if stats:
            print(f"Timing Analysis:")
            print(f"  Dit duration: {stats['dit_ms']} ms")
            print(f"  Dah duration: {stats['dah_ms']} ms (should be ~3x dit)")
            print(f"  Calculated WPM: {stats['wpm']}")
            print(f"  Elements sent: {stats['elements']}")
            print(f"  Characters decoded: {stats['chars']}")
            print()
            
            # Check if timing matches config
            from pathlib import Path
            config_file = Path("config.yaml")
            if config_file.exists():
                with open(config_file, 'r') as f:
                    config = yaml.safe_load(f)
                    config_wpm = config.get('vail_adapter', {}).get('speed_wpm', 25)
                    
                    wpm_diff = abs(stats['wpm'] - config_wpm)
                    if wpm_diff < 2:
                        print(f"✅ Speed matches config: {config_wpm} WPM (diff: {wpm_diff})")
                    else:
                        print(f"⚠️  Speed differs from config: {config_wpm} WPM → {stats['wpm']} WPM")
                        print(f"   Run with --config to reconfigure adapter")
        
        print()
        print(f"Decoded text:")
        print(f"  {decoded if decoded else '(none)'}")
        print()
        print(f"Total events: {self.event_count}")
        print(f"Session duration: {elapsed:.1f} seconds")
        print("="*70)
    
    def run(self):
        """Start analyzer"""
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


def configure_via_midi():
    """Configure Vail adapter via MIDI"""
    if not MIDI_AVAILABLE:
        print("❌ MIDI configuration not available (mido not installed)")
        return False
    
    config_file = Path("config.yaml")
    if not config_file.exists():
        print("❌ config.yaml not found")
        return False
    
    with open(config_file, 'r') as f:
        config = yaml.safe_load(f)
    
    vail_config = config.get('vail_adapter', {})
    if not vail_config.get('enabled', False):
        print("ℹ️  Vail adapter disabled in config")
        return False
    
    # Find device
    output_names = mido.get_output_names()
    device_name = None
    for name in output_names:
        if "XIAO" in name.upper() or "SAMD21" in name.upper():
            device_name = name
            break
    
    if not device_name:
        print("❌ Vail adapter MIDI device not found")
        return False
    
    print(f"✓ Found MIDI device: {device_name}")
    
    try:
        with mido.open_output(device_name) as port:
            keyer_mode = vail_config.get('keyer_mode', 8)
            speed_wpm = vail_config.get('speed_wpm', 25)
            sidetone_note = vail_config.get('sidetone_note', 73)
            output_mode = vail_config.get('output_mode', 'keyboard')
            
            dit_duration = int((1200.0 / speed_wpm) + 0.5)
            dit_duration = max(10, min(127, dit_duration))
            
            print(f"\nSending MIDI configuration:")
            
            output_value = 127 if output_mode == 'keyboard' else 0
            port.send(mido.Message('control_change', control=0, value=output_value))
            print(f"  CC0 = {output_value} (output_mode: {output_mode})")
            
            port.send(mido.Message('program_change', program=keyer_mode))
            print(f"  Program Change = {keyer_mode} (Iambic B)")
            
            port.send(mido.Message('control_change', control=1, value=dit_duration))
            print(f"  CC1 = {dit_duration} ({speed_wpm} WPM)")
            
            port.send(mido.Message('control_change', control=2, value=sidetone_note))
            print(f"  CC2 = {sidetone_note} (sidetone)")
            
            print("\n✅ MIDI configuration sent!")
            return True
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    """Main entry point"""
    configure_midi = '--config' in sys.argv or '-c' in sys.argv
    
    if configure_midi:
        print("="*70)
        print("STEP 1: CONFIGURE VAIL ADAPTER VIA MIDI")
        print("="*70)
        print()
        
        success = configure_via_midi()
        
        if success:
            print("\nWaiting 2 seconds for adapter to apply settings...")
            time.sleep(2)
        
        print()
        print("="*70)
        print("STEP 2: ANALYZE TIMING & DECODE MORSE")
        print("="*70)
        print()
    
    analyzer = VailTimingAnalyzer()
    analyzer.run()


if __name__ == "__main__":
    main()
