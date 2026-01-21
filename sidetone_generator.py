#!/usr/bin/env python3
"""
Sidetone Generator

Generates local audio sidetone for CW keying using PyAudio.
Provides instant audio feedback for manual paddle operation.

Architecture: Uses dedicated audio thread with continuous generation loop
(based on reference implementation from protocol_copy/test_implementation)
for minimal latency and compatibility with blocking keyer timing.
"""

import numpy as np
import pyaudio
import threading


class SidetoneGenerator:
    """
    Real-time sidetone generator with dedicated audio thread
    
    This implementation uses a separate thread that continuously generates
    and writes audio chunks, rather than PyAudio's callback mechanism.
    This provides:
    - Lower latency (2.6ms vs 23ms with callback)
    - Direct flag access (no queue needed)
    - Compatibility with blocking time.sleep() in keyer
    """
    
    def __init__(self, frequency: int = 600, sample_rate: int = 48000, 
                 volume: float = 0.3, device_index=None):
        """
        Initialize sidetone generator
        
        Args:
            frequency: Tone frequency in Hz (typical: 400-800)
            sample_rate: Audio sample rate (48000 recommended)
            volume: Volume level (0.0-1.0)
            device_index: PyAudio device index (None = auto-select)
        """
        self.frequency = frequency
        self.sample_rate = sample_rate
        self.volume = volume
        
        # Audio state
        self.phase = 0.0
        self.key_down = False
        self.envelope = 0.0
        self.target_envelope = 0.0
        
        # Envelope shaping to prevent clicks (optimized for CW)
        self.rise_time = 0.004  # 4ms - fast, clean attack
        self.fall_time = 0.004  # 4ms - fast, clean release
        
        # Simple low-pass filter state for smoother audio
        self.filter_state = 0.0
        self.filter_alpha = 0.1  # Low-pass filter coefficient
        
        # PyAudio setup
        self.audio = pyaudio.PyAudio()
        
        # Auto-select device if not specified
        if device_index is None:
            # Find pipewire or pulse or default device
            for i in range(self.audio.get_device_count()):
                info = self.audio.get_device_info_by_index(i)
                name = info['name'].lower()
                if 'pipewire' in name or 'pulse' in name or info['name'] == 'default':
                    device_index = i
                    print(f"[AUDIO] Auto-selected device {i}: {info['name']}")
                    break
        
        # Open audio stream
        try:
            self.stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=sample_rate,
                output=True,
                output_device_index=device_index,
                frames_per_buffer=128  # Low latency (~2.6ms at 48kHz)
            )
            
            if device_index is not None:
                device_info = self.audio.get_device_info_by_index(device_index)
                print(f"[AUDIO] Using device {device_index}: {device_info['name']}")
            
            print(f"[AUDIO] Stream opened: {sample_rate}Hz, {frequency}Hz tone, volume={volume}")
            
        except Exception as e:
            print(f"[AUDIO ERROR] Failed to open audio stream: {e}")
            raise
        
        # Start audio generation thread
        self.running = True
        self.audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self.audio_thread.start()
    
    def _audio_loop(self):
        """
        Audio generation thread - continuously generates and writes audio
        
        This runs in a separate thread and continuously generates 128-sample
        chunks, providing ~2.6ms latency at 48kHz. Direct access to self.key_down
        flag means no queue latency.
        """
        chunk_size = 128  # Match frames_per_buffer for consistency
        
        # Pre-calculate constants for efficiency (fixed values)
        rise_rate = 1.0 / (self.rise_time * self.sample_rate)
        fall_rate = 1.0 / (self.fall_time * self.sample_rate)
        two_pi = 2.0 * np.pi
        
        while self.running:
            # Recalculate phase_increment each loop to support frequency changes
            phase_increment = self.frequency / self.sample_rate
            
            # Generate audio chunk
            samples = np.zeros(chunk_size, dtype=np.float32)
            
            for i in range(chunk_size):
                # Update target envelope based on key state
                self.target_envelope = 1.0 if self.key_down else 0.0
                
                # Smooth envelope transition (exponential attack/release)
                if self.key_down:
                    # Attack (key down)
                    self.envelope = min(self.envelope + rise_rate, self.target_envelope)
                else:
                    # Release (key up)
                    self.envelope = max(self.envelope - fall_rate, self.target_envelope)
                
                # Generate sine wave only when envelope > 0 (CPU optimization)
                if self.envelope > 0.0001:
                    raw_sample = np.sin(two_pi * self.phase) * self.envelope * self.volume
                    
                    # Simple low-pass filter to smooth audio
                    self.filter_state += self.filter_alpha * (raw_sample - self.filter_state)
                    samples[i] = self.filter_state
                    
                    # Advance phase
                    self.phase += phase_increment
                    if self.phase >= 1.0:
                        self.phase -= 1.0
                else:
                    samples[i] = 0.0
                    self.filter_state = 0.0  # Reset filter when silent
            
            # Output audio
            try:
                self.stream.write(samples.tobytes())
            except Exception:
                pass  # Ignore write errors (e.g., during shutdown)
    
    def set_key(self, key_down: bool):
        """
        Set key state (on/off)
        
        Args:
            key_down: True for key down (tone on), False for key up (tone off)
        
        Note: Direct flag access, no queue latency
        """
        self.key_down = key_down
    
    def set_volume(self, volume: float):
        """
        Set sidetone volume
        
        Args:
            volume: Volume level (0.0-1.0)
        """
        self.volume = max(0.0, min(1.0, volume))
    
    def set_frequency(self, frequency: int):
        """
        Change sidetone frequency
        
        Args:
            frequency: New frequency in Hz
        """
        self.frequency = frequency
        # Phase will adjust naturally in next iteration
    
    def close(self):
        """Stop and close audio stream"""
        self.running = False
        
        # Wait for audio thread to finish
        if hasattr(self, 'audio_thread'):
            self.audio_thread.join(timeout=1.0)
        
        # Clean up PyAudio
        if hasattr(self, 'stream') and self.stream:
            try:
                self.stream.stop_stream()
                self.stream.close()
            except Exception:
                pass
        
        if hasattr(self, 'audio') and self.audio:
            try:
                self.audio.terminate()
            except Exception:
                pass
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
