import json
import os
import sys
import re
import argparse
import warnings
import io
import gc
import traceback

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# Suppress specific warnings that pollute the console output
warnings.filterwarnings("ignore", message=".*urllib3 v2 only supports OpenSSL.*")
warnings.filterwarnings("ignore", message=".*dropout option adds dropout after all but last recurrent layer.*")
warnings.filterwarnings("ignore", message=".*torch.nn.utils.weight_norm is deprecated.*")

from pydub import AudioSegment
from mutagen.id3 import ID3, TIT2, TALB, TRCK
from mutagen.mp3 import MP3
from dotenv import load_dotenv
import numpy as np
import soundfile as sf
import mlx.core as mx

# Load environment variables
load_dotenv()

# Google Cloud TTS has a limit of 5000 bytes per request.
# We'll stick to 4000 characters to be safe for most engines.
MAX_CHARS_PER_REQUEST = 4000

import threading

class TTSModelManager:
    """Manages persistent loading of TTS models per-thread to avoid MLX cross-thread stream conflicts."""
    def __init__(self):
        self._local = threading.local()

    @property
    def kokoro_pipeline(self):
        return getattr(self._local, 'kokoro_pipeline', None)

    @kokoro_pipeline.setter
    def kokoro_pipeline(self, value):
        self._local.kokoro_pipeline = value

    @property
    def pocket_tts_model(self):
        return getattr(self._local, 'pocket_tts_model', None)

    @pocket_tts_model.setter
    def pocket_tts_model(self, value):
        self._local.pocket_tts_model = value

    @property
    def pocket_tts_voice_states(self):
        if not hasattr(self._local, 'pocket_tts_voice_states'):
            self._local.pocket_tts_voice_states = {}
        return self._local.pocket_tts_voice_states

    @pocket_tts_voice_states.setter
    def pocket_tts_voice_states(self, value):
        self._local.pocket_tts_voice_states = value

    @property
    def _last_engine(self):
        return getattr(self._local, '_last_engine', None)

    @_last_engine.setter
    def _last_engine(self, value):
        self._local._last_engine = value

    def clear_models(self):
        """Clears all loaded models from memory for the current thread."""
        print("Clearing loaded models to free RAM...")
        self.kokoro_pipeline = None
        self.pocket_tts_model = None
        if hasattr(self._local, 'pocket_tts_voice_states'):
            self._local.pocket_tts_voice_states.clear()
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            if hasattr(torch, 'mps') and hasattr(torch.mps, 'empty_cache'):
                torch.mps.empty_cache()
        except Exception:
            pass
        gc.collect()

    def get_kokoro(self, model_name="v0.19"):
        if self.kokoro_pipeline is None:
            try:
                print(f"Loading Kokoro model ({model_name})...")
                from kokoro_mlx import KokoroPipeline
                self.kokoro_pipeline = KokoroPipeline(model_name)
            except ImportError:
                # Fallback to older kokoro if installed
                from kokoro import KPipeline
                self.kokoro_pipeline = KPipeline(lang_code='a')
        return self.kokoro_pipeline

    def get_pocket_tts(self):
        if self.pocket_tts_model is None:
            print("Loading Pocket-TTS (MLX) model...")
            from pocket_tts_mlx import TTSModel
            try:
                self.pocket_tts_model = TTSModel.load_model()
            except Exception:
                # If local cache lookup fails due to offline mode, temporarily allow network
                orig_offline = os.environ.get("HF_HUB_OFFLINE", None)
                os.environ["HF_HUB_OFFLINE"] = "0"
                try:
                    import huggingface_hub.constants
                    huggingface_hub.constants.HF_HUB_OFFLINE = False
                except Exception:
                    pass
                try:
                    self.pocket_tts_model = TTSModel.load_model()
                finally:
                    if orig_offline is not None:
                        os.environ["HF_HUB_OFFLINE"] = orig_offline
                        if orig_offline in ("1", "true", "True"):
                            try:
                                import huggingface_hub.constants
                                huggingface_hub.constants.HF_HUB_OFFLINE = True
                            except Exception:
                                pass
                    else:
                        os.environ.pop("HF_HUB_OFFLINE", None)
        return self.pocket_tts_model

    def get_pocket_tts_voice_state(self, voice_name="alba"):
        if voice_name not in self.pocket_tts_voice_states:
            model = self.get_pocket_tts()
            print(f"  Preparing Pocket-TTS voice state for: {voice_name}")
            try:
                state = model.get_state_for_audio_prompt(voice_name, truncate=True)
            except Exception:
                # If local cache lookup fails, temporarily allow network to fetch the voice profile
                orig_offline = os.environ.get("HF_HUB_OFFLINE", None)
                os.environ["HF_HUB_OFFLINE"] = "0"
                try:
                    import huggingface_hub.constants
                    huggingface_hub.constants.HF_HUB_OFFLINE = False
                except Exception:
                    pass
                try:
                    state = model.get_state_for_audio_prompt(voice_name, truncate=True)
                finally:
                    if orig_offline is not None:
                        os.environ["HF_HUB_OFFLINE"] = orig_offline
                        if orig_offline in ("1", "true", "True"):
                            try:
                                import huggingface_hub.constants
                                huggingface_hub.constants.HF_HUB_OFFLINE = True
                            except Exception:
                                pass
                    else:
                        os.environ.pop("HF_HUB_OFFLINE", None)
            self.pocket_tts_voice_states[voice_name] = state
        return self.pocket_tts_voice_states[voice_name]

# Global model manager
model_manager = TTSModelManager()

def _split_oversized_text(text_to_split, max_len):
    """Split oversized sentences by clauses, commas, or words to fit within max_len."""
    if len(text_to_split) <= max_len:
        return [text_to_split]
    
    # Try splitting by punctuation clauses first
    sub_parts = re.split(r'(?<=[,;:\-])\s+', text_to_split)
    result = []
    curr = ""
    for sp in sub_parts:
        if len(sp) > max_len:
            # Word-level fallback
            words = sp.split()
            w_curr = ""
            for w in words:
                if len(w_curr) + len(w) + 1 > max_len:
                    if w_curr.strip():
                        result.append(w_curr.strip())
                    w_curr = w + " "
                else:
                    w_curr += w + " "
            if w_curr.strip():
                curr = w_curr
        elif len(curr) + len(sp) + 1 > max_len:
            if curr.strip():
                result.append(curr.strip())
            curr = sp + " "
        else:
            curr += sp + " "
    if curr.strip():
        result.append(curr.strip())
    return result

def smart_chunk_text(text, max_chars=MAX_CHARS_PER_REQUEST):
    """
    Splits text into chunks of at most max_chars.
    Tries to split by paragraphs first, then by sentences, then by clauses/words.
    """
    chunks = []
    current_chunk = ""
    
    # Split by paragraphs (double newlines or just newlines)
    paragraphs = re.split(r'\n+', text)
    
    for paragraph in paragraphs:
        paragraph = paragraph.strip()
        if not paragraph:
            continue
            
        # If the paragraph itself is too long, split it by sentences
        if len(paragraph) > max_chars:
            # Simple sentence splitting regex (matches ., !, ?)
            sentences = re.split(r'(?<=[.!?])\s+', paragraph)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(sentence) > max_chars:
                    sub_pieces = _split_oversized_text(sentence, max_chars)
                    for piece in sub_pieces:
                        if len(current_chunk) + len(piece) + 1 > max_chars:
                            if current_chunk.strip():
                                chunks.append(current_chunk.strip())
                            current_chunk = piece + " "
                        else:
                            current_chunk += piece + " "
                else:
                    if len(current_chunk) + len(sentence) + 1 > max_chars:
                        if current_chunk.strip():
                            chunks.append(current_chunk.strip())
                        current_chunk = sentence + " "
                    else:
                        current_chunk += sentence + " "
        else:
            if len(current_chunk) + len(paragraph) + 2 > max_chars:
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = paragraph + "\n\n"
            else:
                current_chunk += paragraph + "\n\n"
                
    if current_chunk.strip():
        chunks.append(current_chunk.strip())
        
    return chunks

def synthesize_audio_chunk_kokoro(text_chunk, voice_name, voice_name_2=None, blend_ratio=0.5):
    """Synthesizes a chunk of text using Kokoro-MLX."""
    try:
        pipeline = model_manager.get_kokoro()
        
        actual_voice = voice_name
        if voice_name_2 and str(voice_name_2).lower() != "none" and voice_name_2 != voice_name:
            print(f"    Blending voices: {voice_name} ({1-blend_ratio:.2f}) and {voice_name_2} ({blend_ratio:.2f})")
            try:
                v1 = pipeline.load_voice(voice_name)
                v2 = pipeline.load_voice(voice_name_2)
                actual_voice = v1 * (1 - blend_ratio) + v2 * blend_ratio
            except Exception as e:
                print(f"    Error loading/blending voices, falling back to {voice_name}: {e}")
                actual_voice = voice_name

        # Check if it's the newer KokoroPipeline or older KPipeline
        if hasattr(pipeline, 'generate'):
            generator = pipeline.generate(text_chunk, voice=actual_voice, speed=1, lang='en-us')
            all_audio = []
            for i, (gs, ps, audio) in enumerate(generator):
                if audio is not None:
                    # Ensure audio is an MLX array
                    if not isinstance(audio, mx.array):
                        audio = mx.array(audio)
                    all_audio.append(audio)
        else:
            # Older KPipeline or fallback kokoro package
            generator = pipeline(text_chunk, voice=actual_voice, speed=1, split_pattern=r'\n+')
            all_audio = []
            for i, (gs, ps, audio) in enumerate(generator):
                if audio is not None:
                    # Ensure audio is an MLX array
                    if not isinstance(audio, mx.array):
                        audio = mx.array(audio)
                    all_audio.append(audio)
        
        if not all_audio:
            print("Warning: No audio generated for this chunk.")
            return b""
            
        # Robust concatenation
        try:
            # Ensure all chunks are MLX arrays and have at least 1 dimension
            valid_chunks = []
            for chunk in all_audio:
                if not isinstance(chunk, mx.array):
                    chunk = mx.array(chunk)
                if chunk.ndim == 0:
                    chunk = mx.expand_dims(chunk, 0)
                valid_chunks.append(chunk)
                
            combined_audio = mx.concatenate(valid_chunks)
        except Exception as e:
            print(f"MLX concatenation failed, falling back to numpy: {e}")
            # Fallback to numpy if MLX concatenation fails
            np_chunks = [np.array(a) for a in all_audio]
            combined_audio = mx.array(np.concatenate(np_chunks))
        
        # Convert to WAV bytes
        buffer = io.BytesIO()
        sf.write(buffer, np.array(combined_audio), 24000, format='WAV', subtype='PCM_16')
        buffer.seek(0)
        return buffer.read()
    except Exception as e:
        print(f"Error in Kokoro synthesis: {e}")
        import traceback
        traceback.print_exc()
        return None

def synthesize_audio_chunk_pocket_tts(text_chunk, voice_name="alba"):
    """Synthesizes a chunk of text using Pocket-TTS (MLX)."""
    try:
        model = model_manager.get_pocket_tts()
        voice_state = model_manager.get_pocket_tts_voice_state(voice_name)
        
        # generate_audio with MLX onset optimization parameters
        audio = model.generate_audio(
            voice_state,
            text_chunk,
            warmup_frames=1,
            trim_start_ms=40,
            fade_in_ms=15
        )
        
        if audio is None:
            print("Warning: No audio generated by Pocket-TTS for this chunk.")
            return b""
            
        # Convert MLX array / tensor / ndarray to numpy array
        if isinstance(audio, mx.array):
            audio_np = np.array(audio)
        elif hasattr(audio, 'detach'):
            audio_np = audio.detach().cpu().numpy()
        else:
            audio_np = np.asarray(audio)
            
        if audio_np.size == 0:
            print("Warning: Empty audio generated by Pocket-TTS for this chunk.")
            return b""
            
        sample_rate = getattr(model, 'sample_rate', 24000)
        
        # Convert to WAV bytes
        buffer = io.BytesIO()
        sf.write(buffer, audio_np, sample_rate, format='WAV', subtype='PCM_16')
        buffer.seek(0)
        return buffer.read()
    except Exception as e:
        print(f"Error in Pocket-TTS synthesis: {e}")
        import traceback
        traceback.print_exc()
        return None

def add_id3_tags(file_path, title, album, track_num):
    # Ensure the file has an ID3 tag
    try:
        audio = MP3(file_path, ID3=ID3)
    except Exception:
        audio = MP3(file_path)
    
    if audio.tags is None:
        audio.add_tags()
        
    # TIT2: Title, TALB: Album, TRCK: Track Number
    audio.tags.add(TIT2(encoding=3, text=title))
    audio.tags.add(TALB(encoding=3, text=album))
    audio.tags.add(TRCK(encoding=3, text=str(track_num)))
    
    audio.save()

# Global flag for interrupting generation
interrupted = False

def stop_generation():
    global interrupted
    interrupted = True

def generate_audio_from_manifest(manifest_path, output_dir="Output", tts_engine=None, tts_model=None, voice_name=None, voice_name_2=None, voice_blend_ratio=0.5, progress_callback=None):
    global interrupted
    interrupted = False
    
    if not os.path.exists(manifest_path):
        # Try resource_path if the literal path doesn't exist (e.g. when frozen)
        manifest_path = resource_path(manifest_path)
        
    if not os.path.exists(manifest_path):
        raise FileNotFoundError(f"Error: Manifest file not found - {manifest_path}")
        
    # Normalize engine name
    raw_engine = tts_engine or os.environ.get("TTS_ENGINE", "kokoro")
    tts_engine = raw_engine.lower().replace("_", "-")
    
    if tts_engine == "pocket-tts" or tts_engine == "pockettts":
        voice_name = voice_name or os.environ.get("DEFAULT_VOICE_NAME", "alba")
    else:
        voice_name = voice_name or os.environ.get("DEFAULT_VOICE_NAME", "af_bella")
    
    # Check if engine changed, and clear models if so
    if hasattr(model_manager, '_last_engine') and model_manager._last_engine != tts_engine:
        model_manager.clear_models()
    model_manager._last_engine = tts_engine

    if tts_engine == "kokoro":
        # Pre-load or check availability
        model_manager.get_kokoro()
        if voice_name and voice_name.lower() == "oliver":
            print("  Mapping 'oliver' to Kokoro voice 'bm_lewis'")
            voice_name = "bm_lewis"
        if voice_name_2 and voice_name_2.lower() == "oliver":
            print("  Mapping 'oliver' to Kokoro voice 'bm_lewis'")
            voice_name_2 = "bm_lewis"
    elif tts_engine in ["pocket-tts", "pockettts"]:
        tts_engine = "pocket-tts"
        # Pre-load or check availability
        model_manager.get_pocket_tts()
        model_manager.get_pocket_tts_voice_state(voice_name)
    else:
        raise ValueError(f"Unsupported TTS engine: {tts_engine}")
        
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
        
    book_title = manifest.get("book_title", "Unknown Book")
    chapters = manifest.get("chapters", [])
    approved_chapters = [c for c in chapters if c.get("status") == "approved"]
    total_approved = len(approved_chapters)
    
    if progress_callback:
        progress_callback(0, total_approved, "Initializing...")
    
    clean_book = re.sub(r'[\\/*?:"<>|]', "", book_title).strip() or "Audiobook"
    book_output_dir = os.path.join(output_dir, clean_book)
    os.makedirs(book_output_dir, exist_ok=True)
    
    print(f"Generating audiobook for: {book_title}")
    print(f"Output directory: {book_output_dir}")
    print(f"Using engine: {tts_engine}")
    if voice_name_2 and str(voice_name_2).lower() != "none" and voice_name_2 != voice_name:
        print(f"Using voice: {voice_name} blended with {voice_name_2} (ratio: {voice_blend_ratio})")
    else:
        print(f"Using voice: {voice_name}")
    
    current_processed = 0
    for chapter in chapters:
        if interrupted:
            print("Generation interrupted by user.")
            break

        if chapter.get("status") != "approved":
            continue
            
        current_processed += 1
        if progress_callback:
            progress_callback(current_processed - 1, total_approved, f"Processing {chapter.get('title', 'Chapter')}...")
            
        try:
            ch_index = chapter.get('index', current_processed)
            ch_title = chapter.get('title', f"Chapter {ch_index}")
            print(f"\nProcessing chapter {ch_index}: {ch_title}")
            text = chapter.get('text', '')
            
            chunks = smart_chunk_text(text, max_chars=MAX_CHARS_PER_REQUEST)
            num_chunks = len(chunks)
            print(f"  Split into {num_chunks} chunks for {tts_engine} optimization.")
            
            combined_audio = AudioSegment.empty()
            
            for i, chunk in enumerate(chunks):
                if interrupted: break
                
                if progress_callback:
                    progress_callback(current_processed - 1 + (i/num_chunks), total_approved, f"Chapter {ch_index}: Chunk {i+1}/{num_chunks}...")
                
                print(f"  Synthesizing chunk {i+1}/{num_chunks} with {tts_engine}...")
                if tts_engine == "kokoro":
                    audio_bytes = synthesize_audio_chunk_kokoro(chunk, voice_name, voice_name_2, voice_blend_ratio)
                elif tts_engine == "pocket-tts":
                    audio_bytes = synthesize_audio_chunk_pocket_tts(chunk, voice_name)
                else:
                    raise ValueError(f"Unsupported TTS engine: {tts_engine}")

                if audio_bytes:
                    audio_segment = AudioSegment.from_file(io.BytesIO(audio_bytes), format="wav")
                    combined_audio += audio_segment
                else:
                    print(f"  Warning: Synthesis failed for chunk {i+1}")
                
            if len(combined_audio) == 0:
                print(f"  Warning: No audio could be synthesized for chapter {ch_index}. Skipping export.")
                continue

            # Clean title for filename (support international and unicode titles)
            clean_title = re.sub(r'[\\/*?:"<>|]', "", ch_title).strip() or f"Chapter_{ch_index}"
            filename = f"{ch_index:02d} - {clean_title}.mp3"
            filepath = os.path.join(book_output_dir, filename)
            
            print(f"  Exporting to {filepath}...")
            combined_audio.export(filepath, format="mp3")
            
            print("  Adding ID3 tags...")
            add_id3_tags(filepath, title=ch_title, album=book_title, track_num=ch_index)
            
            if progress_callback:
                progress_callback(current_processed, total_approved, f"Finished {chapter['title']}")
            
            # Clear MLX cache and collect garbage after each CHAPTER
            try:
                import mlx.core as mx
                mx.clear_cache()
                gc.collect()
            except Exception:
                pass
                
        except Exception as e:
            print(f"\nError processing chapter {chapter.get('index', 'unknown')}: {e}")
            traceback.print_exc()
            continue
            
    print("\nAudiobook generation complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate audiobook MP3s from a JSON manifest.")
    parser.add_argument("manifest_file", help="Path to the JSON manifest file")
    parser.add_argument("--model", help="Specific TTS model to use (e.g. tts-1-hd)")
    parser.add_argument("-o", "--output", default="Output", help="Directory to save the generated MP3 files")
    
    args = parser.parse_args()
    
    if args.model:
        os.environ["TTS_MODEL"] = args.model
        
    generate_audio_from_manifest(args.manifest_file, args.output)
