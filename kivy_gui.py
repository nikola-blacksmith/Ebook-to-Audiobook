import os
import sys

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

def get_app_data_dir():
    """ Get a writable directory for app data (logs, config, etc.) """
    if getattr(sys, 'frozen', False):
        # Running as a bundled app on macOS
        app_support = os.path.join(os.path.expanduser('~'), 'Library', 'Application Support', 'EbookToAudiobook')
        os.makedirs(app_support, exist_ok=True)
        return app_support
    else:
        # Running from source
        return os.path.dirname(os.path.abspath(__file__))

# Set KIVY_HOME to a writable directory
os.environ['KIVY_HOME'] = os.path.join(get_app_data_dir(), 'kivy_home')
if not os.path.exists(os.environ['KIVY_HOME']):
    os.makedirs(os.environ['KIVY_HOME'], exist_ok=True)
import json
import threading
import io
import time

try:
    from kivy.app import App
    from kivy.uix.boxlayout import BoxLayout
    from kivy.uix.gridlayout import GridLayout
    from kivy.uix.button import Button
    from kivy.uix.label import Label
    from kivy.uix.textinput import TextInput
    from kivy.uix.togglebutton import ToggleButton
    from kivy.uix.scrollview import ScrollView
    from kivy.uix.popup import Popup
    from kivy.uix.filechooser import FileChooserListView
    from kivy.uix.spinner import Spinner
    from kivy.clock import Clock
    from kivy.uix.progressbar import ProgressBar
    from kivy.core.window import Window
    from kivy.graphics import Color, RoundedRectangle, Rectangle, Mesh, Line
    from kivy.metrics import dp
except ImportError:
    print("Kivy is not installed. Please run 'pip install kivy' to use this GUI.")
    sys.exit(1)

from parse_epub import parse_epub_to_manifest
from generate_audio import generate_audio_from_manifest
# pdf_to_epub is imported lazily inside methods to avoid slow startup

# --- UI Components ---

from kivy.properties import ListProperty
class ModernButton(Button):
    bg_color = ListProperty([0.1, 0.5, 0.8, 1])
    
    def __init__(self, **kwargs):
        if 'bg_color' in kwargs:
            self.bg_color = kwargs.pop('bg_color')
        super(ModernButton, self).__init__(**kwargs)
        self.background_normal = ''
        self.background_color = (0, 0, 0, 0)
        self.color = (1, 1, 1, 1)
        self.bold = True
        self.font_size = dp(16)
        
        with self.canvas.before:
            self.bg_color_instr = Color(*self.bg_color)
            self.bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(8)])
            
        self.bind(pos=self.update_shapes, size=self.update_shapes, state=self.update_shapes, bg_color=self.update_shapes)
        self.update_shapes()
        
    def update_shapes(self, *args):
        color = self.bg_color if self.state == 'normal' else [c*0.8 for c in self.bg_color[:3]] + [self.bg_color[3]]
        self.bg_color_instr.rgba = color
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size

class StyledTextInput(TextInput):
    def __init__(self, **kwargs):
        super(StyledTextInput, self).__init__(**kwargs)
        self.background_normal = ''
        self.background_active = ''
        self.background_color = (0.15, 0.15, 0.18, 1)
        self.foreground_color = (0.9, 0.9, 0.9, 1)
        self.cursor_color = (0, 0.7, 1, 1)
        self.padding = [dp(10), dp(10)]
        self.font_size = dp(14)

from kivy.uix.spinner import SpinnerOption
class CustomSpinnerOption(SpinnerOption):
    def __init__(self, **kwargs):
        super(CustomSpinnerOption, self).__init__(**kwargs)
        self.background_normal = ''
        self.background_down = ''
        self.background_color = (0, 0, 0, 0)
        self.color = (0.9, 0.9, 0.9, 1)
        self.height = dp(45)
        self.size_hint_y = None
        self.font_size = dp(14)
        
        with self.canvas.before:
            self.bg_color_instr = Color(0.2, 0.2, 0.28, 1)
            self.bg_rect = Rectangle(pos=self.pos, size=self.size)
            self.border_color = Color(0.3, 0.3, 0.35, 1)
            self.border_line = Line(points=[self.x, self.y, self.right, self.y], width=1)
            
        self.bind(pos=self.update_shapes, size=self.update_shapes, state=self.update_shapes)
        self.update_shapes()

    def update_shapes(self, *args):
        if self.state == 'normal':
            self.bg_color_instr.rgba = (0.2, 0.2, 0.28, 1)
        else:
            self.bg_color_instr.rgba = (0.3, 0.3, 0.4, 1)
            
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size
        self.border_line.points = [self.x, self.y, self.right, self.y]

class ModernSpinner(Spinner):
    option_cls = CustomSpinnerOption
    
    def __init__(self, **kwargs):
        kwargs.setdefault('background_normal', '')
        kwargs.setdefault('background_down', '')
        kwargs.setdefault('background_color', (0, 0, 0, 0))
        super(ModernSpinner, self).__init__(**kwargs)
        self.color = (1, 1, 1, 1)
        self.font_size = dp(14)
        self.bold = True
        
        with self.canvas.before:
            self.bg_color_instr = Color(0.15, 0.15, 0.22, 1)
            self.bg_rect = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(5)])
            
            self.border_color = Color(0.3, 0.3, 0.4, 1)
            self.border_line = Line(rounded_rectangle=(self.x, self.y, self.width, self.height, dp(5)), width=1.2)
            
            self.arrow_color = Color(0.4, 0.7, 1, 1)
            self.arrow_mesh = Mesh(mode='triangle_fan', vertices=[0]*12, indices=[0, 1, 2])
            
        self.bind(pos=self.update_shapes, size=self.update_shapes, state=self.update_shapes)
        self.update_shapes()

    def update_shapes(self, *args):
        if self.state == 'normal':
            self.bg_color_instr.rgba = (0.15, 0.15, 0.22, 1)
        else:
            self.bg_color_instr.rgba = (0.2, 0.2, 0.3, 1)
            
        self.bg_rect.pos = self.pos
        self.bg_rect.size = self.size
        
        self.border_line.rounded_rectangle = (self.x, self.y, self.width, self.height, dp(5))
        
        # Update arrow position
        arrow_size = dp(10)
        center_y = self.center_y
        center_x = self.right - dp(15)
        v = [
            center_x - arrow_size/2, center_y + arrow_size/4, 0, 0,
            center_x + arrow_size/2, center_y + arrow_size/4, 0, 0,
            center_x, center_y - arrow_size/4, 0, 0
        ]
        self.arrow_mesh.vertices = v

# --- Popups ---

class FileChooserPopup(Popup):
    def __init__(self, callback, title="Select File", filters=['*.*'], **kwargs):
        super(FileChooserPopup, self).__init__(**kwargs)
        self.title = title
        self.size_hint = (0.9, 0.9)
        self.callback = callback
        
        layout = BoxLayout(orientation='vertical', padding=dp(10), spacing=dp(10))
        self.filechooser = FileChooserListView(filters=filters, path=os.getcwd())
        layout.add_widget(self.filechooser)
        
        btn_layout = BoxLayout(size_hint_y=None, height=dp(50), spacing=dp(10))
        cancel_btn = ModernButton(text="Cancel", bg_color=(0.4, 0.4, 0.4, 1))
        cancel_btn.bind(on_release=self.dismiss)
        
        select_btn = ModernButton(text="Select", bg_color=(0.2, 0.6, 0.4, 1))
        select_btn.bind(on_release=self.select_file)
        
        btn_layout.add_widget(cancel_btn)
        btn_layout.add_widget(select_btn)
        
        layout.add_widget(btn_layout)
        self.content = layout
        
    def select_file(self, *args):
        if self.filechooser.selection:
            self.callback(self.filechooser.selection[0])
        self.dismiss()

# --- Main App ---

class RedirectText(object):
    def __init__(self, text_input):
        self.text_input = text_input

    def write(self, string):
        if string:
            Clock.schedule_once(lambda dt: self._update_text(string), 0)

    def _update_text(self, string):
        if not self.text_input:
            return
        self.text_input.text += string
        # Limit text size to prevent performance issues
        if len(self.text_input.text) > 10000:
            self.text_input.text = self.text_input.text[-10000:]
        # Auto-scroll
        self.text_input.cursor = (0, len(self.text_input.text.split('\n')))

    def flush(self):
        pass

class EbookApp(App):
    def build(self):
        Window.size = (1000, 800)
        self.title = "Astra Audiobook Creator"
        self.manifest_path = "manifest.json"
        self.manifest_data = None
        self.is_generating = False
        self.is_converting_pdf = False
        
        # Main Layout with background
        root = BoxLayout(orientation='vertical')
        with root.canvas.before:
            Color(0.08, 0.08, 0.1, 1)
            Rectangle(pos=root.pos, size=Window.size)
            
        main_layout = BoxLayout(orientation='vertical', padding=dp(20), spacing=dp(20))
        
        # Header
        header = Label(
            text="ASTRA AUDIOBOOK CREATOR", 
            size_hint_y=None, height=dp(50),
            font_size=dp(24), bold=True,
            color=(0, 0.8, 1, 1)
        )
        main_layout.add_widget(header)
        
        # TOP SECTION: Unified File Import
        file_section_label = Label(
            text="INPUT FILE  (.epub or .pdf — auto-detected)",
            size_hint_y=None, height=dp(20),
            halign='left', font_size=dp(12),
            color=(0.5, 0.5, 0.6, 1)
        )
        file_section_label.bind(size=file_section_label.setter('text_size'))
        main_layout.add_widget(file_section_label)

        top_section = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(55), spacing=dp(10))

        # Single file path input
        self.epub_path_input = StyledTextInput(
            multiline=False,
            hint_text="Select an .epub or .pdf file..."
        )
        top_section.add_widget(self.epub_path_input)

        browse_btn = ModernButton(
            text="BROWSE",
            size_hint_x=None, width=dp(100),
            bg_color=(0.2, 0.2, 0.25, 1)
        )
        browse_btn.bind(on_release=self.show_file_chooser)
        top_section.add_widget(browse_btn)

        # Smart import button — routes based on file type
        self.parse_btn = ModernButton(
            text="IMPORT & PARSE",
            size_hint_x=None, width=dp(175),
            bg_color=(0.1, 0.4, 0.8, 1)
        )
        self.parse_btn.bind(on_release=self.import_and_parse)
        top_section.add_widget(self.parse_btn)

        self.generate_btn = ModernButton(
            text="GENERATE AUDIO",
            size_hint_x=None, width=dp(175),
            disabled=True,
            bg_color=(0.1, 0.7, 0.4, 1)
        )
        self.generate_btn.bind(on_release=self.generate_audio)
        top_section.add_widget(self.generate_btn)

        main_layout.add_widget(top_section)
        
        # MIDDLE SECTION: Config and Chapters
        middle_section = BoxLayout(orientation='horizontal', spacing=dp(20))
        
        # Left Panel: Settings
        self.settings_panel = BoxLayout(orientation='vertical', size_hint_x=0.35, spacing=dp(15))
        
        settings_label = Label(text="ENGINE SETTINGS", size_hint_y=None, height=dp(30), bold=True, color=(0.4, 0.7, 1, 1))
        self.settings_panel.add_widget(settings_label)
        
        # Engine selection
        engine_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(45), spacing=dp(10))
        engine_row.add_widget(Label(text="Engine:", size_hint_x=None, width=dp(80), color=(0.8, 0.8, 0.8, 1), halign='left'))
        
        self.engine_spinner = ModernSpinner(
            text='Kokoro', 
            values=('Kokoro', 'Pocket-TTS'),
            size_hint_y=None,
            height=dp(45)
        )
        self.engine_spinner.bind(text=self.update_engine_ui)
        engine_row.add_widget(self.engine_spinner)
        self.settings_panel.add_widget(engine_row)
        
        # Voice selection
        voice_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(45), spacing=dp(10))
        voice_row.add_widget(Label(text="Voice:", size_hint_x=None, width=dp(80), color=(0.8, 0.8, 0.8, 1), halign='left'))
        
        self.voice_spinner = ModernSpinner(
            text='af_bella', 
            values=('af_heart', 'af_bella', 'af_nicole', 'af_sky', 'am_adam', 'am_michael', 'bf_emma', 'bf_isabella', 'bm_george', 'bm_lewis'),
            size_hint_y=None,
            height=dp(45)
        )
        voice_row.add_widget(self.voice_spinner)
        self.settings_panel.add_widget(voice_row)
        
        # Voice 2 selection
        voice2_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(45), spacing=dp(10))
        voice2_row.add_widget(Label(text="Voice 2:", size_hint_x=None, width=dp(80), color=(0.8, 0.8, 0.8, 1), halign='left'))
        
        self.voice2_spinner = ModernSpinner(
            text='None', 
            values=('None', 'af_heart', 'af_bella', 'af_nicole', 'af_sky', 'am_adam', 'am_michael', 'bf_emma', 'bf_isabella', 'bm_george', 'bm_lewis'),
            size_hint_y=None,
            height=dp(45)
        )
        voice2_row.add_widget(self.voice2_spinner)
        self.settings_panel.add_widget(voice2_row)

        # Blend Ratio
        blend_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(45), spacing=dp(10))
        blend_row.add_widget(Label(text="Blend:", size_hint_x=None, width=dp(80), color=(0.8, 0.8, 0.8, 1), halign='left'))
        
        from kivy.uix.slider import Slider
        self.blend_slider = Slider(min=0, max=100, value=50, size_hint_x=0.8)
        self.blend_label = Label(text="50%", size_hint_x=0.2, color=(0.9, 0.9, 0.9, 1))
        
        def on_slider_val(instance, value):
            self.blend_label.text = f"{int(value)}%"
        self.blend_slider.bind(value=on_slider_val)
        
        blend_row.add_widget(self.blend_slider)
        blend_row.add_widget(self.blend_label)
        self.settings_panel.add_widget(blend_row)
        
        self.settings_panel.add_widget(Label()) # Spacer
        
        middle_section.add_widget(self.settings_panel)
        
        # Right Panel: Chapter List
        chapter_panel = BoxLayout(orientation='vertical', spacing=dp(10))
        chapter_panel.add_widget(Label(text="CHAPTERS", size_hint_y=None, height=dp(30), bold=True, color=(0.4, 0.7, 1, 1)))
        
        self.chapter_scroll = ScrollView(bar_width=dp(5), bar_color=(0, 0.5, 1, 0.5))
        self.chapter_list = GridLayout(cols=1, spacing=dp(8), size_hint_y=None, padding=[dp(10), 0])
        self.chapter_list.bind(minimum_height=self.chapter_list.setter('height'))
        self.chapter_scroll.add_widget(self.chapter_list)
        
        # Background for scroll area
        scroll_bg = BoxLayout(orientation='vertical')
        with scroll_bg.canvas.before:
            Color(0.12, 0.12, 0.15, 1)
            RoundedRectangle(pos=scroll_bg.pos, size=scroll_bg.size, radius=[dp(10)])
        scroll_bg.bind(pos=self._update_scroll_bg, size=self._update_scroll_bg)
        scroll_bg.add_widget(self.chapter_scroll)
        
        chapter_panel.add_widget(scroll_bg)
        middle_section.add_widget(chapter_panel)
        
        main_layout.add_widget(middle_section)
        
        # BOTTOM SECTION: Progress and Logs
        bottom_section = BoxLayout(orientation='vertical', size_hint_y=None, height=dp(250), spacing=dp(10))
        
        # Progress and Status
        prog_row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(40), spacing=dp(20))
        self.status_label = Label(text="System Ready", halign='left', color=(0.6, 0.9, 1, 1))
        self.status_label.bind(size=self.status_label.setter('text_size'))
        prog_row.add_widget(self.status_label)
        
        self.stop_btn = ModernButton(text="STOP", size_hint_x=None, width=dp(80), bg_color=(0.8, 0.2, 0.2, 1), disabled=True)
        self.stop_btn.bind(on_release=self.stop_generation)
        prog_row.add_widget(self.stop_btn)
        bottom_section.add_widget(prog_row)
        
        self.progress_bar = ProgressBar(max=100, value=0, size_hint_y=None, height=dp(15))
        bottom_section.add_widget(self.progress_bar)
        
        # Logs
        self.log_text = TextInput(
            readonly=True, background_color=(0.05, 0.05, 0.07, 1), 
            foreground_color=(0.7, 0.8, 0.7, 1),
            font_size=dp(12), padding=[dp(10), dp(10)]
        )
        bottom_section.add_widget(self.log_text)
        
        main_layout.add_widget(bottom_section)
        root.add_widget(main_layout)
        
        # Initial voice setup
        self.update_engine_ui(None, self.engine_spinner.text)
        
        # Redirect stdout
        try:
            sys.stdout = RedirectText(self.log_text)
            sys.stderr = RedirectText(self.log_text)
        except Exception as e:
            print(f"Warning: Could not redirect stdout: {e}")
        
        # Load manifest if exists
        Clock.schedule_once(lambda dt: self.load_manifest(), 0.5)
        
        return root

    def _update_scroll_bg(self, instance, value):
        instance.canvas.before.clear()
        with instance.canvas.before:
            Color(0.12, 0.12, 0.15, 1)
            RoundedRectangle(pos=instance.pos, size=instance.size, radius=[dp(10)])

    def update_engine_ui(self, spinner, text):
        print(f"Engine selection changed: {text}")
        is_pocket_tts = (text == 'Pocket-TTS')
        
        pocket_voices = ('alba', 'azelma', 'cosette', 'eponine', 'fantine', 'javert', 'jean', 'marius')
        
        voices = {
            'Kokoro': ('af_heart', 'af_bella', 'af_nicole', 'af_sky', 'am_adam', 'am_michael', 'bf_emma', 'bf_isabella', 'bm_george', 'bm_lewis'),
            'Pocket-TTS': pocket_voices
        }
        
        engine_voices = voices.get(text, ('af_bella',))
        self.voice_spinner.values = engine_voices
        if self.voice_spinner.text not in engine_voices:
            self.voice_spinner.text = 'alba' if is_pocket_tts else ('af_bella' if 'af_bella' in engine_voices else engine_voices[0])
        
        if is_pocket_tts:
            self.voice2_spinner.values = ('None',)
            self.voice2_spinner.text = 'None'
            self.voice2_spinner.disabled = True
            self.voice2_spinner.opacity = 0.4
            self.blend_slider.disabled = True
            self.blend_slider.opacity = 0.4
            self.blend_label.text = "N/A"
            self.blend_label.opacity = 0.4
        else:
            v2_values = ['None'] + list(engine_voices)
            self.voice2_spinner.values = tuple(v2_values)
            self.voice2_spinner.disabled = False
            self.voice2_spinner.opacity = 1.0
            self.blend_slider.disabled = False
            self.blend_slider.opacity = 1.0
            self.blend_label.text = f"{int(self.blend_slider.value)}%"
            self.blend_label.opacity = 1.0
            if self.voice2_spinner.text not in self.voice2_spinner.values:
                self.voice2_spinner.text = 'None'
        
        # Force layout update to prevent interaction issues
        if hasattr(self, 'settings_panel'):
            Clock.schedule_once(lambda dt: self.settings_panel.do_layout(), 0.1)
            Clock.schedule_once(lambda dt: self.settings_panel.parent.do_layout() if self.settings_panel.parent else None, 0.1)

    def show_file_chooser(self, instance):
        """Open a file chooser that accepts both .epub and .pdf files."""
        FileChooserPopup(
            callback=self.set_input_path,
            title="Select ePub or PDF File",
            filters=['*.epub', '*.pdf']
        ).open()

    def set_input_path(self, path):
        """Populate the file input and update the import button label to match the file type."""
        self.epub_path_input.text = path
        if path.lower().endswith('.pdf'):
            self.parse_btn.text = "CONVERT & PARSE"
        else:
            self.parse_btn.text = "IMPORT & PARSE"

    def import_and_parse(self, instance):
        """
        Smart router: if the selected file is a .pdf, run the PDF → ePub
        OCR pipeline first, then parse the resulting ePub. If it's already
        an .epub, go directly to parsing.
        """
        file_path = self.epub_path_input.text.strip()
        if not file_path:
            print("Error: No file selected.")
            return

        if file_path.lower().endswith('.pdf'):
            self._start_pdf_pipeline(file_path)
        elif file_path.lower().endswith('.epub'):
            self.parse_epub(instance)
        else:
            print(f"Error: Unsupported file type. Please select a .epub or .pdf file.")

    def _start_pdf_pipeline(self, pdf_path):
        """Kick off the PDF → ePub → parse pipeline in a background thread."""
        if self.is_converting_pdf:
            return

        self.is_converting_pdf = True
        self.parse_btn.disabled = True
        self.generate_btn.disabled = True
        self.log_text.text = ""
        self._set_status("Starting PDF → ePub conversion via Unlimited-OCR...")
        print("Starting PDF → ePub conversion via Unlimited-OCR...")
        print("Note: The first run will download the model (~7 GB). Subsequent runs load from local cache.")

        threading.Thread(
            target=self._run_pdf_conversion,
            args=(pdf_path,),
            daemon=True
        ).start()

    def _run_pdf_conversion(self, pdf_path):
        """Background worker: OCR → ePub, then hand off to the epub parser."""
        try:
            from pdf_to_epub import convert_pdf_to_epub

            def on_progress(msg):
                Clock.schedule_once(lambda dt: self._set_status(msg), 0)

            epub_path = convert_pdf_to_epub(
                pdf_path,
                output_epub_path=None,  # saved alongside the PDF
                progress_callback=on_progress
            )

            def finish(dt):
                # Update the file path field to show the resulting ePub
                self.epub_path_input.text = epub_path
                self.parse_btn.text = "IMPORT & PARSE"
                self._set_status(f"Conversion complete! Parsing ePub...")
                print(f"\nePub saved to: {epub_path}")
                print("Parsing ePub automatically...")
                self.parse_epub(None)

            Clock.schedule_once(finish, 0)

        except Exception as e:
            import traceback
            traceback.print_exc()
            Clock.schedule_once(lambda dt: self._set_status(f"PDF Conversion Error: {e}"), 0)
        finally:
            self.is_converting_pdf = False
            Clock.schedule_once(lambda dt: setattr(self.parse_btn, 'disabled', False), 0)

    def parse_epub(self, instance):
        epub_path = self.epub_path_input.text.strip()
        if not epub_path:
            print("Error: No EPUB file selected.")
            return
            
        self.parse_btn.disabled = True
        self.log_text.text = ""
        self.status_label.text = "Parsing EPUB..."
        
        threading.Thread(target=self._run_parse, args=(epub_path,), daemon=True).start()
        
    def _run_parse(self, epub_path):
        try:
            parse_epub_to_manifest(epub_path, self.manifest_path)
            Clock.schedule_once(lambda dt: self.load_manifest(), 0)
            Clock.schedule_once(lambda dt: self._set_status("Parsing complete."), 0)
        except Exception as e:
            print(f"Error: {e}")
            Clock.schedule_once(lambda dt: self._set_status(f"Error: {e}"), 0)
        finally:
            Clock.schedule_once(lambda dt: setattr(self.parse_btn, 'disabled', False), 0)

    def load_manifest(self):
        if not os.path.exists(self.manifest_path):
            return
            
        try:
            with open(self.manifest_path, 'r', encoding='utf-8') as f:
                self.manifest_data = json.load(f)
                
            self.chapter_list.clear_widgets()
            for i, chapter in enumerate(self.manifest_data.get("chapters", [])):
                row = BoxLayout(orientation='horizontal', size_hint_y=None, height=dp(45), spacing=dp(10))
                
                title = chapter.get("title", "Unknown")
                words = chapter.get("word_count", 0)
                status = chapter.get("status", "pending")
                
                row.add_widget(Label(text=f"{chapter.get('index', i+1)}. {title}", halign='left', size_hint_x=0.6))
                row.add_widget(Label(text=f"{words} words", size_hint_x=0.2, color=(0.5, 0.5, 0.5, 1)))
                
                is_approved = (status == "approved")
                btn_color = (0.1, 0.6, 0.3, 1) if is_approved else (0.6, 0.2, 0.2, 1)
                toggle = ToggleButton(
                    text="APPROVED" if is_approved else "REJECTED",
                    state='down' if is_approved else 'normal',
                    size_hint_x=0.2, background_color=(0,0,0,0)
                )
                
                # Custom drawing for toggle button
                with toggle.canvas.before:
                    toggle.bg_color = Color(*btn_color)
                    toggle.rect = RoundedRectangle(pos=toggle.pos, size=toggle.size, radius=[dp(5)])
                
                def update_toggle_ui(instance, value):
                    instance.rect.pos = instance.pos
                    instance.rect.size = instance.size
                toggle.bind(pos=update_toggle_ui, size=update_toggle_ui)
                
                def on_toggle(instance, idx=i):
                    if instance.state == 'down':
                        instance.text = "APPROVED"
                        instance.bg_color.rgba = (0.1, 0.6, 0.3, 1)
                        self.manifest_data["chapters"][idx]["status"] = "approved"
                    else:
                        instance.text = "REJECTED"
                        instance.bg_color.rgba = (0.6, 0.2, 0.2, 1)
                        self.manifest_data["chapters"][idx]["status"] = "rejected"
                    self.save_manifest()
                
                toggle.bind(on_release=on_toggle)
                row.add_widget(toggle)
                self.chapter_list.add_widget(row)
                
            self.generate_btn.disabled = False
            print(f"Loaded {len(self.manifest_data['chapters'])} chapters.")
        except Exception as e:
            print(f"Load Error: {e}")

    def save_manifest(self):
        if self.manifest_data:
            tmp_path = self.manifest_path + ".tmp"
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(self.manifest_data, f, indent=4, ensure_ascii=False)
            os.replace(tmp_path, self.manifest_path)

    def generate_audio(self, instance):
        if self.is_generating: return
        
        self.is_generating = True
        self.generate_btn.disabled = True
        self.stop_btn.disabled = False
        self.progress_bar.value = 0
        
        engine = self.engine_spinner.text.lower()
        voice = self.voice_spinner.text
        voice2 = self.voice2_spinner.text
        blend_ratio = self.blend_slider.value / 100.0
        
        print(f"Starting generation with {engine}...")
        threading.Thread(target=self._run_generate, args=(engine, voice, voice2, blend_ratio), daemon=True).start()

    def _run_generate(self, engine, voice, voice2, blend_ratio):
        try:
            def on_progress(percent_done, total, msg):
                # percent_done is 0.0 to total_chapters
                actual_percent = (percent_done / total) * 100
                Clock.schedule_once(lambda dt: self._update_progress(actual_percent, msg), 0)
            
            generate_audio_from_manifest(
                self.manifest_path, 
                tts_engine=engine, 
                voice_name=voice,
                voice_name_2=voice2,
                voice_blend_ratio=blend_ratio,
                progress_callback=on_progress
            )
            Clock.schedule_once(lambda dt: self._set_status("Generation complete!"), 0)
            Clock.schedule_once(lambda dt: self._update_progress(100, "Done."), 0)
        except Exception as e:
            print(f"Generation Error: {e}")
            Clock.schedule_once(lambda dt: self._set_status(f"Error: {e}"), 0)
        finally:
            self.is_generating = False
            Clock.schedule_once(lambda dt: setattr(self.generate_btn, 'disabled', False), 0)
            Clock.schedule_once(lambda dt: setattr(self.stop_btn, 'disabled', True), 0)

    def stop_generation(self, instance):
        from generate_audio import stop_generation
        print("Stopping generation (will finish current chunk)...")
        stop_generation()
        self.stop_btn.disabled = True
        self._set_status("Stopping generation...")

    def _update_progress(self, val, msg):
        self.progress_bar.value = val
        self.status_label.text = msg

    def _set_status(self, msg):
        self.status_label.text = msg

if __name__ == '__main__':
    EbookApp().run()
