import sys
import json
import os
import argparse
from bs4 import BeautifulSoup
import ebooklib
from ebooklib import epub

def extract_title_and_text(html_content, fallback_title="Chapter"):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # Try to find a human-readable title from headings or title tag
    detected_title = None
    for tag_name in ['h1', 'h2', 'title', 'h3']:
        tag = soup.find(tag_name)
        if tag and tag.get_text().strip():
            candidate = " ".join(tag.get_text().split())
            if len(candidate) >= 2 and len(candidate) <= 120:
                detected_title = candidate
                break
                
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.extract()
        
    text = soup.get_text(separator=' ')
    lines = (line.strip() for line in text.splitlines())
    chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
    text = '\n'.join(chunk for chunk in chunks if chunk)
    
    final_title = detected_title or fallback_title
    return final_title, text

def parse_epub_to_manifest(epub_path, output_manifest="manifest.json"):
    if not os.path.exists(epub_path):
        raise FileNotFoundError(f"Error: File not found - {epub_path}")
        
    print(f"Parsing EPUB: {epub_path}...")
    book = epub.read_epub(epub_path)
    
    # Try to get book title for metadata
    book_title = "Unknown Title"
    title_metadata = book.get_metadata('DC', 'title')
    if title_metadata:
        book_title = title_metadata[0][0]
    
    print(f"Book Title detected: {book_title}")
    
    manifest = {
        "book_title": book_title,
        "chapters": []
    }
    
    chapter_index = 1
    
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        html_content = item.get_content().decode('utf-8', errors='ignore')
        fallback = os.path.splitext(os.path.basename(item.file_name))[0]
        title, text_content = extract_title_and_text(html_content, fallback_title=fallback)
        
        # Skip empty documents
        if len(text_content.strip()) < 50:
            continue
            
        chapter_data = {
            "index": chapter_index,
            "title": title,
            "text": text_content,
            "word_count": len(text_content.split()),
            "status": "pending" # pending | approved | rejected
        }
        
        manifest["chapters"].append(chapter_data)
        print(f"Found document: {title} (Words: {chapter_data['word_count']})")
        chapter_index += 1
        
    tmp_manifest = output_manifest + ".tmp"
    with open(tmp_manifest, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, indent=4, ensure_ascii=False)
    os.replace(tmp_manifest, output_manifest)
        
    print(f"\nSuccessfully generated {output_manifest}")
    print(f"Total documents extracted: {len(manifest['chapters'])}")
    print("Next step: Open manifest.json, review the chapters, and change the status to 'approved' for the chapters you want to generate audio for. Change the status to 'rejected' or just delete the entries you want to skip.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse an EPUB file into a JSON manifest.")
    parser.add_argument("epub_file", help="Path to the EPUB file")
    parser.add_argument("-o", "--output", default="manifest.json", help="Path to the output JSON manifest (default: manifest.json)")
    
    args = parser.parse_args()
    parse_epub_to_manifest(args.epub_file, args.output)
