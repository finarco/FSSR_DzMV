# DMV Processor - Inštalácia pre Windows

## Rýchla inštalácia (odporúčané)

### Možnosť A: Pomocou winget (Windows 10/11)
```powershell
winget install oschwartz10612.Poppler
```

### Možnosť B: Pomocou Chocolatey
```powershell
choco install poppler
```

---

## Manuálna inštalácia

### 1. Stiahnite Poppler pre Windows

Stiahnite z: https://github.com/oschwartz10612/poppler-windows/releases/

- Vyberte najnovšiu verziu (napr. `Release-24.08.0-0.zip`)
- Rozbaľte do `C:\poppler`

### 2. Pridajte do PATH

**Možnosť A: Cez GUI**
1. Stlačte `Win + R`, napíšte `sysdm.cpl` a stlačte Enter
2. Kliknite na záložku "Rozšírené" (Advanced)
3. Kliknite "Premenné prostredia" (Environment Variables)
4. V časti "Systémové premenné" nájdite `Path` a kliknite "Upraviť"
5. Kliknite "Nový" a pridajte: `C:\poppler\Library\bin`
6. Kliknite OK vo všetkých oknách

**Možnosť B: Cez PowerShell (ako admin)**
```powershell
[Environment]::SetEnvironmentVariable("Path", $env:Path + ";C:\poppler\Library\bin", "Machine")
```

### 3. Overte inštaláciu

Otvorte nový terminál a spustite:
```cmd
pdftoppm -v
```

Mal by sa zobraziť výstup ako: `pdftoppm version 24.08.0`

---

## Inštalácia Python závislostí

```bash
pip install pdf2image pytesseract pillow pdfplumber
```

## Pre OCR (voliteľné)

### Inštalácia Tesseract OCR

1. Stiahnite z: https://github.com/UB-Mannheim/tesseract/wiki
2. Spustite inštalátor (vyberte "Add to PATH")
3. Počas inštalácie zaškrtnite jazyky: Slovak, Czech, English

Alebo cez Chocolatey:
```powershell
choco install tesseract
```

---

## Konfigurácia DMV Processor

Ak máte Poppler v inej ceste, upravte `config.json`:

```json
{
    "server": {
        "port": 5100,
        "host": "localhost"
    },
    "poppler_path": "C:\\poppler\\Library\\bin",
    "tesseract_path": "C:\\Program Files\\Tesseract-OCR"
}
```

---

## Spustenie

```bash
cd dmv_processor
python dmv_server.py
```

Otvorte prehliadač: http://localhost:5100/dmv_gui.html

---

## Riešenie problémov

### "Unable to get page count. Is poppler installed and in PATH?"
- Skontrolujte či je `C:\poppler\Library\bin` v PATH
- Reštartujte terminál/PowerShell po zmene PATH
- Skúste: `where pdftoppm`

### "Tesseract not found"
- Skontrolujte či je Tesseract v PATH
- Skúste: `where tesseract`

### PDF sa nezobrazuje
- Skontrolujte konzolu prehliadača (F12) pre chyby
- Skontrolujte výstup servera v termináli
