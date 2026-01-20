#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DMV Processor - Web Server pre GUI
===================================
Spustenie: python dmv_server.py
Server: http://localhost:5100 (konfigurovateľné v config.json)
"""

import os, sys, json, re, tempfile, base64
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

from dmv_processor import (
    DMVProcessor, KalkulatorDane, RegisterConnector,
    Spolocnost, Vozidlo, Adresa
)

# Načítaj konfiguráciu
CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'config.json')
CONFIG = {
    'server': {'port': 5100, 'host': 'localhost'},
    'poppler_path': None,  # None = použije systémovú PATH
    'tesseract_path': None
}
if os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
        loaded = json.load(f)
        CONFIG.update(loaded)

# Nastav tesseract path ak je definovaný
if CONFIG.get('tesseract_path'):
    import pytesseract
    pytesseract.pytesseract.tesseract_cmd = os.path.join(CONFIG['tesseract_path'], 'tesseract.exe')

class DMVHandler(SimpleHTTPRequestHandler):
    processor = DMVProcessor()
    register = RegisterConnector()
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/overit':
            self.handle_overit(parsed.query)
        else:
            super().do_GET()
    
    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == '/api/upload-pdf':
            self.handle_upload_pdf()
        else:
            self.send_error(404)
    
    def send_json(self, data, status=200):
        response = json.dumps(data, ensure_ascii=False)
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(response.encode('utf-8'))
    
    def handle_overit(self, query_string):
        """Overí spoločnosť v RÚZ/RPO."""
        params = parse_qs(query_string)
        ico_dic = params.get('ico', params.get('dic', ['']))[0]
        
        if not ico_dic:
            self.send_json({'success': False, 'error': 'Zadajte IČO alebo DIČ'}, 400)
            return
        
        ico = re.sub(r'\D', '', ico_dic)
        if len(ico) == 10 and ico.startswith('20'):
            ico = ico[2:]
        
        print(f"[API] Overujem IČO: {ico}")
        
        # Skús najprv RÚZ (má DIČ a kompletnú adresu)
        data = self.register.vyhladaj_v_ruz_podla_ico(ico)
        source = "RÚZ"
        
        # Ak nie, skús RPO
        if not data:
            data = self.register.vyhladaj_v_rpo_podla_ico(ico)
            source = "RPO"
        
        if data:
            print(f"[API] Nájdené v {source}: {data.get('nazov', 'N/A')}")
            self.send_json({
                'success': True, 'source': source,
                'data': {
                    'ico': data.get('ico', ''),
                    'dic': data.get('dic', ''),
                    'nazov': data.get('nazov', ''),
                    'ulica': data.get('ulica', ''),
                    'cislo': data.get('cislo', ''),
                    'psc': data.get('psc', ''),
                    'obec': data.get('obec', ''),
                }
            })
        else:
            self.send_json({'success': False, 'error': 'Spoločnosť nenájdená'})
    
    def handle_upload_pdf(self):
        """Spracuje nahraný PDF."""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length)
            
            content_type = self.headers.get('Content-Type', '')
            
            if 'application/json' in content_type:
                # JSON s base64 encoded PDF
                data = json.loads(body.decode('utf-8'))
                
                if 'file' not in data:
                    self.send_json({'success': False, 'error': 'Žiadny súbor'}, 400)
                    return
                
                pdf_data = base64.b64decode(data['file'])
                rok = int(data.get('rok', 2024))
                
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                    tmp.write(pdf_data)
                    tmp_path = tmp.name
                    
            elif 'multipart/form-data' in content_type:
                # Parsovanie multipart manuálne
                boundary = content_type.split('boundary=')[1].encode()
                parts = body.split(b'--' + boundary)
                
                pdf_data = None
                rok = 2024
                
                for part in parts:
                    if b'filename=' in part and b'.pdf' in part.lower():
                        # Nájdi začiatok dát (po prázdnom riadku)
                        header_end = part.find(b'\r\n\r\n')
                        if header_end != -1:
                            pdf_data = part[header_end + 4:].rstrip(b'\r\n--')
                    elif b'name="rok"' in part:
                        header_end = part.find(b'\r\n\r\n')
                        if header_end != -1:
                            rok = int(part[header_end + 4:].strip().rstrip(b'\r\n--'))
                
                if not pdf_data:
                    self.send_json({'success': False, 'error': 'Žiadny PDF súbor'}, 400)
                    return
                
                with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                    tmp.write(pdf_data)
                    tmp_path = tmp.name
            else:
                self.send_json({'success': False, 'error': 'Neplatný Content-Type'}, 400)
                return
            
            print(f"[API] Spracúvam PDF, rok={rok}")
            
            # Extrahuj text a obrázky
            extracted_text = ""
            page_images_base64 = []
            
            # 1. Konvertuj PDF na obrázky (vždy - pre náhľad)
            try:
                from pdf2image import convert_from_path
                from PIL import Image
                import io
                
                # Cesta k poppler - z configu alebo default
                poppler_path = CONFIG.get('poppler_path')
                if not poppler_path:
                    # Skús štandardné cesty
                    import platform
                    if platform.system() == 'Windows':
                        possible_paths = [
                            r'C:\poppler\Library\bin',
                            r'C:\Program Files\poppler\Library\bin',
                            r'C:\Program Files\poppler-24.08.0\Library\bin',
                            os.path.expanduser(r'~\poppler\Library\bin'),
                        ]
                        for p in possible_paths:
                            if os.path.exists(p):
                                poppler_path = p
                                break
                    else:
                        poppler_path = '/usr/bin'
                
                print(f"[API] Poppler path: {poppler_path}")
                
                # Konvertuj PDF na obrázky
                print(f"[API] Konvertujem PDF na obrázky: {tmp_path}")
                images = convert_from_path(
                    tmp_path, 
                    dpi=150, 
                    fmt='png',
                    poppler_path=poppler_path
                )
                print(f"[API] Konvertovaných {len(images)} strán")
                
                for i, image in enumerate(images):
                    try:
                        # Zmenši pre prenos
                        max_size = (800, 1100)
                        image.thumbnail(max_size, Image.Resampling.LANCZOS)
                        
                        # Konvertuj na base64
                        buffer = io.BytesIO()
                        image.save(buffer, format='PNG', optimize=True)
                        buffer.seek(0)
                        img_base64 = base64.b64encode(buffer.read()).decode('utf-8')
                        page_images_base64.append(img_base64)
                        print(f"[API] Strana {i+1}: {len(img_base64)} bytes base64")
                    except Exception as e:
                        print(f"[API] Chyba pri spracovaní strany {i+1}: {e}")
                    
                print(f"[API] Vytvorených {len(page_images_base64)} náhľadov")
                
            except ImportError as e:
                print(f"[API] pdf2image nie je nainštalované: {e}")
            except Exception as e:
                print(f"[API] Chyba konverzie na obrázky: {e}")
                import traceback
                traceback.print_exc()
            
            # 2. Skús pdfplumber (pre textové PDF)
            try:
                import pdfplumber
                with pdfplumber.open(tmp_path) as pdf:
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            extracted_text += page_text + "\n\n"
            except Exception as e:
                print(f"[API] pdfplumber chyba: {e}")
            
            # 3. Ak text je príliš krátky, skús OCR
            if len(extracted_text.strip()) < 50:
                print("[API] Text príliš krátky, skúšam OCR...")
                try:
                    from pdf2image import convert_from_path
                    import pytesseract
                    from PIL import Image, ImageEnhance, ImageFilter
                    
                    # Konvertuj PDF na obrázky (vyššie DPI pre OCR)
                    poppler_path = CONFIG.get('poppler_path') or '/usr/bin'
                    images_hires = convert_from_path(tmp_path, dpi=300, poppler_path=poppler_path)
                    ocr_text = ""
                    
                    for i, image in enumerate(images_hires):
                        print(f"[API] OCR strana {i+1}/{len(images_hires)}...")
                        
                        # Predspracovanie obrázka pre lepšie OCR
                        gray = image.convert('L')
                        enhancer = ImageEnhance.Contrast(gray)
                        enhanced = enhancer.enhance(2.0)
                        sharpened = enhanced.filter(ImageFilter.SHARPEN)
                        
                        # OCR
                        custom_config = r'--oem 3 --psm 6'
                        page_text = pytesseract.image_to_string(
                            sharpened, 
                            lang='eng',
                            config=custom_config
                        )
                        
                        if page_text:
                            ocr_text += f"--- Strana {i+1} ---\n{page_text}\n\n"
                    
                    if ocr_text.strip():
                        extracted_text = ocr_text
                        print(f"[API] OCR úspešné, extrahovaných {len(ocr_text)} znakov")
                    
                except ImportError as e:
                    print(f"[API] OCR knižnice nie sú dostupné: {e}")
                    extracted_text += "\n\n[OCR nie je dostupné]"
                except Exception as e:
                    print(f"[API] OCR chyba: {e}")
                    extracted_text += f"\n\n[OCR chyba: {e}]"
            
            if not extracted_text.strip():
                extracted_text = "[Nepodarilo sa extrahovať text z PDF]"
            
            # Spracuj PDF cez processor
            try:
                spolocnost, vozidla = self.processor.spracuj_pdf(tmp_path, over_v_registri=False)
            except Exception as e:
                print(f"[API] Chyba spracovania: {e}")
                spolocnost = Spolocnost()
                vozidla = []
            
            kalkulator = KalkulatorDane(rok)
            for v in vozidla:
                kalkulator.vypocitaj_dan_pre_vozidlo(v)
            
            os.unlink(tmp_path)
            
            result = {
                'success': True,
                'text': extracted_text,
                'images': page_images_base64,
                'spolocnost': {
                    'dic': spolocnost.dic or '',
                    'ico': '',
                    'nazov': ' '.join(spolocnost.po_obchodne_meno) if spolocnost.po_obchodne_meno else '',
                    'ulica': spolocnost.sidlo.ulica or '',
                    'cislo': spolocnost.sidlo.cislo or '',
                    'psc': spolocnost.sidlo.psc or '',
                    'obec': spolocnost.sidlo.obec or '',
                },
                'vozidla': [{
                    'evc': v.evc or '',
                    'kategoria': v.kategoria or 'M1',
                    'datum': v.datum_prvej_evidencie or '',
                    'datumVzniku': v.datum_vzniku_povinnosti or f'1.1.{rok}',
                    'objem': v.objem_valcov or 0,
                    'hmotnost': v.hmotnost or 0,
                    'mesiace': v.pocet_mesiacov_1 or 12,
                    'hybrid': v.hybrid,
                    'plyn': v.plyn,
                    'kombi': v.kombi_doprava,
                    'zakladnaSadzba': v.sadzba or 0,
                    'sadzba': v.rocna_sadzba_1 or 0,
                    'dan': v.dan_1 or 0,
                    'vek': kalkulator.vypocitaj_vek_v_mesiacoch(v.datum_prvej_evidencie),
                } for v in vozidla]
            }
            
            print(f"[API] Extrahované: {len(vozidla)} vozidiel")
            self.send_json(result)
            
        except Exception as e:
            print(f"[API] Chyba: {e}")
            self.send_json({'success': False, 'error': str(e)}, 500)
    
    def log_message(self, format, *args):
        print(f"[HTTP] {args[0]}")

def run_server(port=None):
    if port is None:
        port = CONFIG.get('server', {}).get('port', 5100)
    httpd = HTTPServer(('', port), DMVHandler)
    print(f"""
╔══════════════════════════════════════════════════════════╗
║         DMV Processor v2.0 - Web Server                  ║
╠══════════════════════════════════════════════════════════╣
║  Server: http://localhost:{port:<5}                        ║
║  Otvorte túto adresu v prehliadači.                     ║
║  Ukončenie: Ctrl+C                                       ║
╚══════════════════════════════════════════════════════════╝
""")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n[Server] Ukončujem...")

if __name__ == '__main__':
    run_server(int(sys.argv[1]) if len(sys.argv) > 1 else None)
