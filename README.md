# DMV Processor v2.0 - Daň z motorových vozidiel SR

## 🚀 Rýchly štart

```bash
# 1. Inštalácia závislostí
pip install flask pdfplumber lxml

# 2. Spustenie servera
cd dmv_processor
python dmv_server.py

# 3. Otvorte v prehliadači
http://localhost:5100
```

## Funkcie

| Funkcia | Popis |
|---------|-------|
| 📂 **Načítanie PDF** | Extrakcia údajov z dokumentov |
| 🔍 **Overenie ORSR/RÚZ** | Automatické doplnenie údajov firmy |
| 💰 **Výpočet sadzieb** | Podľa zákona 361/2014 Z.z. |
| ✏️ **Úprava údajov** | Kontrola a oprava pred exportom |
| 📤 **Export XML** | Podľa schémy dmv2025.xsd |

## Súbory

| Súbor | Popis |
|-------|-------|
| `dmv_server.py` | Flask web server |
| `dmv_gui.html` | Webové rozhranie |
| `dmv_gui.js` | JavaScript logika |
| `dmv_processor.py` | Hlavná knižnica + CLI |

## CLI použitie

```bash
# Výpočet dane
python dmv_processor.py vypocet -k M1 -o 1998 -d 15.3.2020 -r 2024

# Overenie firmy
python dmv_processor.py over 31322832

# Demo XML
python dmv_processor.py demo -r 2024
```

## Sadzby M1 (2024)

| Objem | Sadzba | Vek 0-36m | Vek 36-72m |
|-------|--------|-----------|------------|
| do 900 cm³ | 62 € | 46.50 € | 49.60 € |
| 900-1200 cm³ | 80 € | 60.00 € | 64.00 € |
| 1200-1500 cm³ | 115 € | 86.25 € | 92.00 € |
| 1500-2000 cm³ | 148 € | 111.00 € | 118.40 € |
| 2000-3000 cm³ | 180 € | 135.00 € | 144.00 € |

## Požiadavky

- Python 3.8+
- Flask
- pdfplumber
- lxml
