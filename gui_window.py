#!/usr/bin/env python3
"""
Tkinter GUI for TCI CW Controller
Provides status display, F-key macro editor, and live settings adjustment
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import asyncio
import threading
import logging

logger = logging.getLogger(__name__)


class TkinterGUI:
    """
    Main GUI window for TCI CW Controller
    
    Features:
    - Status indicators (TCI/USB connection)
    - F-key macro editor (F1-F12 buttons + text fields)
    - Live settings (CW speed, sidetone freq/volume)
    - Asyncio integration via root.after() polling
    """
    
    def __init__(self, controller, loop):
        """
        Initialize GUI window
        
        Args:
            controller: TCICWController instance (for state access and control)
            loop: asyncio event loop (for run_coroutine_threadsafe)
        """
        self.controller = controller
        self.loop = loop
        self.root = tk.Tk()
        self.root.title("TCI CW Controller")
        self.root.geometry("800x600")
        
        # Configuration cache (edited in memory, saved on button)
        self.config = controller.config.copy()
        
        # Status variables
        self.tci_status_var = tk.StringVar(value="Disconnected")
        self.tci_color_var = tk.StringVar(value="red")
        self.usb_status_var = tk.StringVar(value="Disconnected")
        self.usb_color_var = tk.StringVar(value="red")
        self.active_macro_var = tk.StringVar(value="Idle")
        
        # Macro text widgets (for live editing)
        self.macro_entries = {}  # F1-F12 -> Text widget
        self.macro_preview_labels = {}  # F1-F12 -> Label (with callsign substitution)
        
        # Setting variables (will be updated after controller initializes)
        self.cw_speed_var = tk.IntVar(value=25)
        self.sidetone_freq_var = tk.IntVar(value=600)
        self.sidetone_vol_var = tk.DoubleVar(value=50.0)
        
        # Throttling for slider updates (prevent flooding)
        self._speed_update_pending = None
        self._sidetone_update_pending = None
        
        # Build UI
        self._create_widgets()
        
        # Load actual values from controller after short delay (let it initialize)
        self.root.after(1000, self._load_initial_values)
        
        # Start asyncio polling
        self._schedule_asyncio_step()
        self._schedule_status_update()
        
    def _create_widgets(self):
        """Build all GUI components"""
        # Main container with scrollbar
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)
        
        # Three sections: Status, Macros, Settings
        self._create_status_frame(main_frame)
        self._create_macros_frame(main_frame)
        self._create_settings_frame(main_frame)
        
        # Bottom buttons
        self._create_bottom_buttons(main_frame)
        
    def _create_status_frame(self, parent):
        """Status indicators for TCI and USB connections"""
        frame = ttk.LabelFrame(parent, text="Status", padding="10")
        frame.grid(row=0, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # TCI Connection
        ttk.Label(frame, text="TCI Server:").grid(row=0, column=0, sticky=tk.W, padx=5)
        self.tci_indicator = tk.Label(frame, textvariable=self.tci_status_var, 
                                      bg="red", fg="white", width=15, relief=tk.SUNKEN)
        self.tci_indicator.grid(row=0, column=1, padx=5)
        
        # USB Paddle
        ttk.Label(frame, text="USB Paddle:").grid(row=1, column=0, sticky=tk.W, padx=5)
        self.usb_indicator = tk.Label(frame, textvariable=self.usb_status_var,
                                      bg="red", fg="white", width=15, relief=tk.SUNKEN)
        self.usb_indicator.grid(row=1, column=1, padx=5)
        
        # Active Macro
        ttk.Label(frame, text="Active:").grid(row=2, column=0, sticky=tk.W, padx=5)
        ttk.Label(frame, textvariable=self.active_macro_var, 
                 font=('TkDefaultFont', 10, 'bold')).grid(row=2, column=1, sticky=tk.W, padx=5)
        
    def _create_macros_frame(self, parent):
        """F-key macro buttons and text editors"""
        frame = ttk.LabelFrame(parent, text="F-Key Macros", padding="10")
        frame.grid(row=1, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), pady=5)
        parent.rowconfigure(1, weight=1)
        
        # Create 12 macro editors (4 columns x 3 rows)
        for i in range(1, 13):
            row = (i - 1) // 4
            col = (i - 1) % 4
            
            fkey = f"F{i}"
            self._create_macro_editor(frame, fkey, row, col)
            
    def _create_macro_editor(self, parent, fkey, row, col):
        """Create single F-key editor widget"""
        # Container frame
        container = ttk.Frame(parent)
        container.grid(row=row, column=col, padx=5, pady=5, sticky=(tk.W, tk.E, tk.N, tk.S))
        
        # Button to send macro
        btn = ttk.Button(container, text=fkey, width=6,
                        command=lambda: self._send_macro(fkey))
        btn.grid(row=0, column=0, sticky=tk.W)
        
        # Text entry (single line)
        text_widget = tk.Entry(container, width=30)
        text_widget.grid(row=1, column=0, sticky=(tk.W, tk.E), pady=2)
        
        # Load current macro text
        current_text = self.config['function_keys'].get(fkey, "")
        text_widget.insert(0, current_text)
        
        # Bind change event for preview update
        text_widget.bind('<KeyRelease>', lambda e: self._update_preview(fkey))
        
        # Preview label (shows with callsign substitution)
        preview = ttk.Label(container, text="", foreground="gray", font=('TkDefaultFont', 8))
        preview.grid(row=2, column=0, sticky=tk.W)
        
        # Character count label
        char_count = ttk.Label(container, text="", foreground="blue", font=('TkDefaultFont', 8))
        char_count.grid(row=3, column=0, sticky=tk.W)
        
        # Store references
        self.macro_entries[fkey] = text_widget
        self.macro_preview_labels[fkey] = (preview, char_count)
        
        # Initial preview
        self._update_preview(fkey)
        
        # Make column expandable
        container.columnconfigure(0, weight=1)
        
    def _create_settings_frame(self, parent):
        """Live settings adjustment (sliders)"""
        frame = ttk.LabelFrame(parent, text="Settings", padding="10")
        frame.grid(row=2, column=0, sticky=(tk.W, tk.E), pady=5)
        
        # CW Speed
        ttk.Label(frame, text="CW Speed (WPM):").grid(row=0, column=0, sticky=tk.W, padx=5)
        speed_scale = ttk.Scale(frame, from_=15, to=40, orient=tk.HORIZONTAL,
                               variable=self.cw_speed_var, command=lambda v: self._on_speed_change(v))
        speed_scale.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5)
        self.speed_label = ttk.Label(frame, text=f"{self.cw_speed_var.get()} WPM")
        self.speed_label.grid(row=0, column=2, padx=5)
        
        # Sidetone Frequency
        ttk.Label(frame, text="Sidetone Freq (Hz):").grid(row=1, column=0, sticky=tk.W, padx=5)
        freq_scale = ttk.Scale(frame, from_=400, to=800, orient=tk.HORIZONTAL,
                              variable=self.sidetone_freq_var, command=lambda v: self._on_freq_change(v))
        freq_scale.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5)
        self.freq_label = ttk.Label(frame, text=f"{self.sidetone_freq_var.get()} Hz")
        self.freq_label.grid(row=1, column=2, padx=5)
        
        # Sidetone Volume
        ttk.Label(frame, text="Sidetone Volume (%):").grid(row=2, column=0, sticky=tk.W, padx=5)
        vol_scale = ttk.Scale(frame, from_=0, to=100, orient=tk.HORIZONTAL,
                             variable=self.sidetone_vol_var, command=lambda v: self._on_volume_change(v))
        vol_scale.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=5)
        self.vol_label = ttk.Label(frame, text=f"{self.sidetone_vol_var.get():.0f}%")
        self.vol_label.grid(row=2, column=2, padx=5)
        
        # Note about Vail adapter
        note_label = ttk.Label(frame, text="Note: Vail adapter sidetone updates require firmware reset",
                              foreground="gray", font=('TkDefaultFont', 8))
        note_label.grid(row=3, column=0, columnspan=3, sticky=tk.W, padx=5, pady=(5,0))
        
        # Make sliders expandable
        frame.columnconfigure(1, weight=1)
        
    def _create_bottom_buttons(self, parent):
        """Save/Load/Quit buttons"""
        button_frame = ttk.Frame(parent)
        button_frame.grid(row=3, column=0, sticky=(tk.W, tk.E), pady=10)
        
        ttk.Button(button_frame, text="Save Config", 
                  command=self._save_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Reload Config", 
                  command=self._reload_config).pack(side=tk.LEFT, padx=5)
        ttk.Button(button_frame, text="Quit", 
                  command=self._quit).pack(side=tk.RIGHT, padx=5)
        
    # Callbacks
    
    def _load_initial_values(self):
        """Load initial slider values from controller after initialization"""
        try:
            # Reload config from controller
            self.config = self.controller.config.copy()
            
            # Update slider values
            self.cw_speed_var.set(self.config['cw']['speed_wpm'])
            self.sidetone_freq_var.set(self.config['sidetone']['frequency'])
            self.sidetone_vol_var.set(self.config['sidetone']['volume'] * 100)
            
            # Update labels
            self.speed_label.config(text=f"{self.cw_speed_var.get()} WPM")
            self.freq_label.config(text=f"{self.sidetone_freq_var.get()} Hz")
            self.vol_label.config(text=f"{self.sidetone_vol_var.get():.0f}%")
            
            logger.info(f"[GUI] Loaded initial values: {self.cw_speed_var.get()} WPM, "
                       f"{self.sidetone_freq_var.get()} Hz, {self.sidetone_vol_var.get():.0f}%")
        except Exception as e:
            logger.error(f"[GUI] Error loading initial values: {e}")
    
    def _update_preview(self, fkey):
        """Update preview label with callsign substitution and character count"""
        text_widget = self.macro_entries[fkey]
        preview_label, char_label = self.macro_preview_labels[fkey]
        
        text = text_widget.get()
        
        # Substitute callsign
        callsign = self.config['operator']['callsign']
        preview_text = text.replace('{callsign}', callsign)
        
        # Update preview
        if preview_text:
            preview_label.config(text=f"Preview: {preview_text}")
        else:
            preview_label.config(text="(empty)")
            
        # Character count and warning
        char_count = len(preview_text)
        if char_count > 100:
            char_label.config(text=f"{char_count} chars (⚠ long message)", foreground="red")
        elif char_count > 50:
            char_label.config(text=f"{char_count} chars (caution)", foreground="orange")
        else:
            char_label.config(text=f"{char_count} chars", foreground="blue")
            
    def _send_macro(self, fkey):
        """Send F-key macro via controller"""
        # Get current text from entry widget
        text = self.macro_entries[fkey].get().strip()
        
        if not text:
            messagebox.showwarning("Empty Macro", f"{fkey} has no text configured")
            return
            
        # Substitute callsign
        callsign = self.config['operator']['callsign']
        message = text.replace('{callsign}', callsign)
        
        # Update active macro indicator
        self.active_macro_var.set(f"{fkey}: Sending...")
        
        # Send via controller (async)
        future = asyncio.run_coroutine_threadsafe(
            self.controller.send_macro(message),
            self.loop
        )
        
        # Clear indicator after send
        self.root.after(500, lambda: self.active_macro_var.set("Idle"))
        
        logger.info(f"[GUI] {fkey} clicked: {message}")
        
    def _on_speed_change(self, value):
        """CW speed slider changed - with throttling"""
        wpm = int(float(value))
        self.speed_label.config(text=f"{wpm} WPM")
        
        # Cancel pending update
        if self._speed_update_pending:
            self.root.after_cancel(self._speed_update_pending)
        
        # Schedule update after 300ms of no changes (debounce)
        self._speed_update_pending = self.root.after(300, lambda: self._apply_speed_change(wpm))
    
    def _apply_speed_change(self, wpm):
        """Actually apply speed change (called after debounce delay)"""
        logger.info(f"[GUI] Applying speed change to {wpm} WPM")
        
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.controller.update_cw_speed(wpm),
                self.loop
            )
        except Exception as e:
            logger.error(f"[GUI] Error queueing speed update: {e}")
        
        self._speed_update_pending = None
        
    def _on_freq_change(self, value):
        """Sidetone frequency slider changed - with throttling"""
        freq = int(float(value))
        self.freq_label.config(text=f"{freq} Hz")
        
        # Cancel pending update
        if self._sidetone_update_pending:
            self.root.after_cancel(self._sidetone_update_pending)
        
        # Schedule update after 300ms of no changes (debounce)
        self._sidetone_update_pending = self.root.after(300, lambda: self._apply_sidetone_change())
    
    def _on_volume_change(self, value):
        """Sidetone volume slider changed - with throttling"""
        vol_percent = float(value)
        self.vol_label.config(text=f"{vol_percent:.0f}%")
        
        # Cancel pending update
        if self._sidetone_update_pending:
            self.root.after_cancel(self._sidetone_update_pending)
        
        # Schedule update after 300ms of no changes (debounce)
        self._sidetone_update_pending = self.root.after(300, lambda: self._apply_sidetone_change())
    
    def _apply_sidetone_change(self):
        """Actually apply sidetone changes (called after debounce delay)"""
        freq = self.sidetone_freq_var.get()
        vol = self.sidetone_vol_var.get() / 100.0
        
        logger.info(f"[GUI] Applying sidetone change: {freq} Hz, {vol*100:.0f}%")
        
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.controller.update_sidetone(freq, vol),
                self.loop
            )
        except Exception as e:
            logger.error(f"[GUI] Error updating sidetone: {e}")
        
        self._sidetone_update_pending = None
            
    def _save_config(self):
        """Save current config to file"""
        # Update config dict from UI widgets
        for fkey, text_widget in self.macro_entries.items():
            self.config['function_keys'][fkey] = text_widget.get()
            
        self.config['cw']['speed_wpm'] = self.cw_speed_var.get()
        self.config['sidetone']['frequency'] = self.sidetone_freq_var.get()
        self.config['sidetone']['volume'] = self.sidetone_vol_var.get() / 100.0
        
        # Save via controller
        future = asyncio.run_coroutine_threadsafe(
            self.controller.save_config_to_file(self.config),
            self.loop
        )
        
        try:
            future.result(timeout=2.0)
            messagebox.showinfo("Config Saved", "Configuration saved to config.yaml")
            logger.info("[GUI] Config saved successfully")
        except Exception as e:
            messagebox.showerror("Save Failed", f"Error saving config: {e}")
            logger.error(f"[GUI] Config save failed: {e}")
            
    def _reload_config(self):
        """Reload config from file"""
        result = messagebox.askyesno("Reload Config", 
                                     "Reload config from file? Unsaved changes will be lost.")
        if not result:
            return
            
        # Reload via controller
        future = asyncio.run_coroutine_threadsafe(
            self.controller.reload_config(),
            self.loop
        )
        
        try:
            new_config = future.result(timeout=2.0)
            self.config = new_config.copy()
            
            # Update UI widgets
            for fkey, text_widget in self.macro_entries.items():
                text_widget.delete(0, tk.END)
                text_widget.insert(0, self.config['function_keys'].get(fkey, ""))
                self._update_preview(fkey)
                
            # Update slider values
            self.cw_speed_var.set(self.config['cw']['speed_wpm'])
            self.sidetone_freq_var.set(self.config['sidetone']['frequency'])
            self.sidetone_vol_var.set(self.config['sidetone']['volume'] * 100)
            
            # Update slider labels manually (set() doesn't trigger callbacks)
            self.speed_label.config(text=f"{self.config['cw']['speed_wpm']} WPM")
            self.freq_label.config(text=f"{self.config['sidetone']['frequency']} Hz")
            self.vol_label.config(text=f"{self.config['sidetone']['volume']*100:.0f}%")
            
            # Actually apply the reloaded settings to hardware/TCI
            logger.info("[GUI] Applying reloaded settings to hardware...")
            try:
                # Apply speed
                asyncio.run_coroutine_threadsafe(
                    self.controller.update_cw_speed(self.config['cw']['speed_wpm']),
                    self.loop
                )
                
                # Apply sidetone
                asyncio.run_coroutine_threadsafe(
                    self.controller.update_sidetone(
                        self.config['sidetone']['frequency'],
                        self.config['sidetone']['volume']
                    ),
                    self.loop
                )
            except Exception as e:
                logger.error(f"[GUI] Error applying reloaded settings: {e}")
            
            messagebox.showinfo("Config Reloaded", "Configuration reloaded from config.yaml")
            logger.info("[GUI] Config reloaded successfully")
        except Exception as e:
            messagebox.showerror("Reload Failed", f"Error reloading config: {e}")
            logger.error(f"[GUI] Config reload failed: {e}")
            
    def _quit(self):
        """Quit application"""
        result = messagebox.askyesno("Quit", "Quit TCI CW Controller?")
        if result:
            logger.info("[GUI] User quit via GUI")
            self.root.quit()
            
    # Status updates
    
    def _update_status(self):
        """Poll controller state and update status indicators"""
        try:
            # TCI connection status
            if self.controller.tci_client and self.controller.tci_client.ready:
                self.tci_status_var.set("Connected")
                self.tci_indicator.config(bg="green")
            else:
                self.tci_status_var.set("Disconnected")
                self.tci_indicator.config(bg="red")
                
            # USB paddle status
            if self.controller.usb_paddle_handler and self.controller.usb_paddle_handler.running:
                self.usb_status_var.set("Connected")
                self.usb_indicator.config(bg="green")
            else:
                self.usb_status_var.set("Disconnected")
                self.usb_indicator.config(bg="red")
                
        except Exception as e:
            logger.error(f"[GUI] Status update error: {e}")
            
        # Schedule next update (100ms)
        self.root.after(100, self._update_status)
        
    # Asyncio integration
    
    def _schedule_asyncio_step(self):
        """Run pending asyncio tasks (polling integration)"""
        try:
            # Process pending tasks without blocking
            # Note: This requires the event loop to be running in a background thread
            # The loop itself is managed by main.py's asyncio.run(controller.run())
            pass  # No-op - event loop runs independently in background
        except Exception as e:
            logger.error(f"[GUI] Asyncio step error: {e}")
            
        # Schedule next step (50ms for responsive async tasks)
        self.root.after(50, self._schedule_asyncio_step)
        
    def _schedule_status_update(self):
        """Start status polling"""
        self._update_status()
        
    def run(self):
        """Start GUI main loop"""
        logger.info("[GUI] Starting Tkinter main loop")
        self.root.mainloop()
        logger.info("[GUI] Tkinter main loop exited")
