#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DMV Processor - Spracovanie dane z motorových vozidiel pre SR
=============================================================
Program na extrakciu údajov z PDF a generovanie XML pre finančnú správu SR.

Funkcie:
- Extrakcia údajov z PDF (text/OCR)
- Overenie a doplnenie údajov z ORSR / Register účtovných závierok
- Automatický výpočet sadzieb dane podľa zákona 361/2014 Z.z.
- Generovanie XML pre finančnú správu SR (dmv2025.xsd)
- SQLite databáza pre ukladanie údajov

Autor: Claude AI Assistant
Verzia: 2.0
"""

import os
import re
import json
import sqlite3
import urllib.request
import urllib.parse
from datetime import datetime, date
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any, Tuple
from lxml import etree
from html.parser import HTMLParser
import pdfplumber

# Pokus o import OCR knižníc (voliteľné)
try:
    import pytesseract
    from pdf2image import convert_from_path
    OCR_AVAILABLE = True
except ImportError:
    OCR_AVAILABLE = False


# =============================================================================
# DÁTOVÉ MODELY
# =============================================================================

@dataclass
class Adresa:
    """Adresa sídla alebo organizačnej zložky."""
    ulica: str = ""
    cislo: str = ""
    psc: str = ""
    obec: str = ""
    stat: str = "Slovenská republika"
    telefon: str = ""
    email_fax: str = ""


@dataclass
class Spolocnost:
    """Údaje o spoločnosti / daňovníkovi."""
    # Typ osoby
    fo: bool = False  # Fyzická osoba
    po: bool = True   # Právnická osoba
    zahranicna: bool = False  # Zahraničná osoba
    
    # Identifikácia
    dic: str = ""
    datum_narodenia: str = ""  # Pre FO
    
    # Názov (FO)
    fo_priezvisko: str = ""
    fo_meno: str = ""
    fo_titul: str = ""
    fo_titul_za: str = ""
    fo_obchodne_meno: str = ""
    
    # Názov (PO) - až 4 riadky
    po_obchodne_meno: List[str] = field(default_factory=list)
    
    # Sídlo
    sidlo: Adresa = field(default_factory=Adresa)
    
    # Adresa organizačnej zložky
    adresa_org_zlozky: Adresa = field(default_factory=Adresa)
    
    # ID pre databázu
    id: Optional[int] = None


@dataclass
class Vozidlo:
    """Údaje o motorovom vozidle pre daňové priznanie."""
    # Dátumy
    datum_prvej_evidencie: str = ""  # r01
    datum_vzniku_povinnosti: str = ""  # r02 vzniku
    datum_zaniku_povinnosti: str = ""  # r02 zaniku
    
    # Kategória vozidla
    kategoria: str = ""  # r03 - L, M1, M2, M3, N1, N2, N3, O1-O4
    
    # Kód druhu vozidla (r04)
    kod_druhu_ba_bb: bool = False  # BA-BB
    kod_druhu_bc_bd: bool = False  # BC-BD
    
    # Pruženie (r05)
    vzduchove_pruzenie: bool = False
    ine_systemy: bool = False
    
    # Základné údaje
    evc: str = ""  # r06 - Evidenčné číslo vozidla
    objem_valcov: float = 0.0  # r07 - v cm³
    vykon_motora: float = 0.0  # r08 - v kW
    hmotnost: float = 0.0  # r09 - v kg
    pocet_naprav: int = 0  # r10
    
    # Určenie sadzby
    r11_pismeno: str = ""  # Písmeno pre sadzbu
    r12_pismeno: str = ""  # Zníženie/zvýšenie sadzby
    r12_oslobodene: bool = False  # Oslobodené od dane
    
    # Sadzba dane
    sadzba: int = 0  # r13 - Ročná sadzba v EUR
    
    # Zvýšenie/zníženie sadzby (r14) - Stĺpec 1
    zvysenie_1_10: bool = False
    zvysenie_1_20: bool = False
    zvysenie_1_30: bool = False
    zvysenie_1_40: bool = False
    zvysenie_1_50: bool = False
    
    # Zvýšenie/zníženie sadzby (r14) - Stĺpec 2
    zvysenie_2_10: bool = False
    zvysenie_2_20: bool = False
    zvysenie_2_30: bool = False
    zvysenie_2_40: bool = False
    zvysenie_2_50: bool = False
    
    # Ročná sadzba po úprave (r15)
    rocna_sadzba_1: float = 0.0
    rocna_sadzba_2: float = 0.0
    
    # Zníženie sadzby pre ekologické vozidlá (r16)
    hybrid: bool = False
    plyn: bool = False
    vodik: bool = False
    
    # Sadzba po znížení (r17)
    sadzba_po_znizeni_1: float = 0.0
    sadzba_po_znizeni_2: float = 0.0
    
    # Kombinovaná doprava (r18)
    kombi_doprava: bool = False
    
    # Sadzba po znížení pre kombi dopravu (r19)
    sadzba_kombi_1: float = 0.0
    sadzba_kombi_2: float = 0.0
    
    # Počet mesiacov (r20a)
    pocet_mesiacov_1: int = 0
    pocet_mesiacov_2: int = 0
    
    # Počet dní (r20b)
    pocet_dni_1: int = 0
    pocet_dni_2: int = 0
    
    # Daň (r21)
    dan_1: float = 0.0
    dan_2: float = 0.0
    
    # Sumarizácia
    r22: float = 0.0  # Daň spolu
    r23: float = 0.0  # Oslobodenie
    r24: float = 0.0  # Daň po oslobodení
    r25: float = 0.0  # Predpísané preddavky
    
    # ID pre databázu
    id: Optional[int] = None
    spolocnost_id: Optional[int] = None


# =============================================================================
# SADZBY DANE - Zákon 361/2014 Z.z. v znení neskorších predpisov
# =============================================================================

class SadzbyDane:
    """
    Sadzby dane z motorových vozidiel podľa zákona 361/2014 Z.z.
    Platné pre zdaňovacie obdobie 2024 a 2025.
    """
    
    # Príloha č. 1 - Sadzby pre kategóriu L a M1 (osobné vozidlá) podľa objemu valcov
    SADZBY_M1_OBJEM = [
        # (od, do, sadzba_eur)
        (0, 150, 50),
        (150, 900, 62),
        (900, 1200, 80),
        (1200, 1500, 115),
        (1500, 2000, 148),
        (2000, 3000, 180),
        (3000, float('inf'), 218),
    ]
    
    # Príloha č. 1a - Sadzby pre kategóriu N1 podľa hmotnosti a počtu náprav
    SADZBY_N1 = [
        (0, 2, 2, 115),
        (2, 4, 2, 148),
        (4, 6, 2, 180),
        (6, 8, 2, 218),
        (8, 10, 2, 253),
        (10, 12, 2, 295),
    ]
    
    # Príloha č. 1b - Sadzby pre kategóriu M2, N2
    SADZBY_M2_N2 = [
        (0, 2, 2, 115),
        (2, 4, 2, 148),
        (4, 6, 2, 180),
        (6, 8, 2, 218),
        (8, 10, 2, 253),
        (10, 12, 2, 295),
    ]
    
    # Príloha č. 1c - Sadzby pre kategóriu M3, N3 BA/BB
    SADZBY_M3_N3_BA_BB = [
        (12, 14, 2, 358), (14, 16, 2, 417), (16, 18, 2, 483),
        (18, 20, 2, 552), (20, 22, 2, 625), (22, 24, 2, 702),
        (24, 26, 2, 793),
        (12, 14, 3, 295), (14, 16, 3, 365), (16, 18, 3, 417),
        (18, 20, 3, 483), (20, 22, 3, 552), (22, 24, 3, 625),
        (24, 26, 3, 702), (26, 28, 3, 793), (28, 30, 3, 902),
        (30, 32, 3, 1019),
        (18, 20, 4, 417), (20, 22, 4, 483), (22, 24, 4, 552),
        (24, 26, 4, 625), (26, 28, 4, 702), (28, 30, 4, 793),
        (30, 32, 4, 902), (32, 34, 4, 1019), (34, 36, 4, 1166),
        (36, 38, 4, 1282), (38, 40, 4, 1417),
    ]
    
    # Príloha č. 1e - Sadzby pre kategóriu O (prípojné vozidlá)
    SADZBY_O = {'O1': 50, 'O2': 115, 'O3': 180, 'O4': 295}
    
    # Úprava sadzby podľa veku - rok 2024
    UPRAVA_PODLA_VEKU_2024 = [
        (0, 36, 0.75),      # -25%
        (36, 72, 0.80),     # -20%
        (72, 108, 0.85),    # -15%
        (108, 144, 1.00),   # bez zmeny
        (144, 156, 1.10),   # +10%
        (156, float('inf'), 1.20),  # +20%
    ]
    
    # Úprava sadzby podľa veku - rok 2025 (po novele)
    UPRAVA_PODLA_VEKU_2025 = [
        (0, 36, 1.00),      # štandardná
        (36, 72, 1.10),     # +10%
        (72, 108, 1.20),    # +20%
        (108, 144, 1.30),   # +30%
        (144, 180, 1.40),   # +40%
        (180, float('inf'), 1.50),  # +50%
    ]
    
    @classmethod
    def get_zakladna_sadzba_m1(cls, objem_cm3: float) -> int:
        for od, do, sadzba in cls.SADZBY_M1_OBJEM:
            if od < objem_cm3 <= do:
                return sadzba
        return cls.SADZBY_M1_OBJEM[-1][2]
    
    @classmethod
    def get_zakladna_sadzba_n1(cls, hmotnost_t: float, napravy: int) -> int:
        for od, do, nap, sadzba in cls.SADZBY_N1:
            if od < hmotnost_t <= do and nap == napravy:
                return sadzba
        return 115
    
    @classmethod
    def get_zakladna_sadzba_o(cls, kategoria: str) -> int:
        return cls.SADZBY_O.get(kategoria, 50)
    
    @classmethod
    def get_koeficient_veku(cls, mesiace: int, rok: int = 2024) -> float:
        upravy = cls.UPRAVA_PODLA_VEKU_2025 if rok >= 2025 else cls.UPRAVA_PODLA_VEKU_2024
        for od, do, koef in upravy:
            if od <= mesiace < do:
                return koef
        return upravy[-1][2]


class KalkulatorDane:
    """Kalkulátor dane z motorových vozidiel."""
    
    def __init__(self, rok: int = 2024):
        self.rok = rok
        self.koniec_obdobia = date(rok, 12, 31)
    
    def vypocitaj_vek_v_mesiacoch(self, datum_prvej_evidencie: str) -> int:
        if not datum_prvej_evidencie:
            return 0
        try:
            parts = datum_prvej_evidencie.replace('/', '.').split('.')
            if len(parts) >= 3:
                datum = date(int(parts[2]), int(parts[1]), int(parts[0]))
                mesiace = (self.koniec_obdobia.year - datum.year) * 12
                mesiace += self.koniec_obdobia.month - datum.month
                return max(0, mesiace)
        except (ValueError, IndexError):
            pass
        return 0
    
    def get_zakladna_sadzba(self, vozidlo: Vozidlo) -> int:
        kategoria = vozidlo.kategoria.upper() if vozidlo.kategoria else ""
        
        # Elektromobily
        if vozidlo.vykon_motora > 0 and vozidlo.objem_valcov == 0:
            if kategoria in ['L', 'M1', 'N1'] or kategoria.startswith('L'):
                return 0
        
        # M1 a L - podľa objemu
        if kategoria in ['L', 'M1'] or kategoria.startswith('L'):
            return SadzbyDane.get_zakladna_sadzba_m1(vozidlo.objem_valcov)
        
        hmotnost_t = vozidlo.hmotnost / 1000 if vozidlo.hmotnost else 0
        napravy = vozidlo.pocet_naprav or 2
        
        # N1
        if kategoria == 'N1':
            return SadzbyDane.get_zakladna_sadzba_n1(hmotnost_t, napravy)
        
        # O kategórie
        if kategoria.startswith('O'):
            return SadzbyDane.get_zakladna_sadzba_o(kategoria)
        
        # Default
        if vozidlo.objem_valcov > 0:
            return SadzbyDane.get_zakladna_sadzba_m1(vozidlo.objem_valcov)
        return 115
    
    def vypocitaj_dan(self, vozidlo: Vozidlo, mesiacov_pouzitia: int = 12) -> Dict[str, Any]:
        result = {
            'zakladna_sadzba': 0, 'vek_mesiacov': 0, 'koeficient_veku': 1.0,
            'sadzba_po_veku': 0.0, 'koeficient_eko': 1.0, 'sadzba_po_eko': 0.0,
            'sadzba_finalna': 0.0, 'mesiacov': mesiacov_pouzitia, 'dan': 0.0,
            'r11_pismeno': '', 'zvysenie_percento': 0,
        }
        
        zakladna = self.get_zakladna_sadzba(vozidlo)
        result['zakladna_sadzba'] = zakladna
        if zakladna == 0:
            return result
        
        vek = self.vypocitaj_vek_v_mesiacoch(vozidlo.datum_prvej_evidencie)
        result['vek_mesiacov'] = vek
        
        kategoria = vozidlo.kategoria.upper() if vozidlo.kategoria else ""
        
        # O4 v 2024 má -60%
        if kategoria == 'O4' and self.rok == 2024:
            koef_veku = 0.40
        elif kategoria.startswith('O'):
            koef_veku = 1.0
        else:
            koef_veku = SadzbyDane.get_koeficient_veku(vek, self.rok)
        
        result['koeficient_veku'] = koef_veku
        result['sadzba_po_veku'] = zakladna * koef_veku
        
        if koef_veku < 1.0:
            result['zvysenie_percento'] = int((1.0 - koef_veku) * -100)
        else:
            result['zvysenie_percento'] = int((koef_veku - 1.0) * 100)
        
        # Ekologické vozidlá -50%
        koef_eko = 1.0
        if vozidlo.hybrid or vozidlo.plyn or vozidlo.vodik:
            if self.rok >= 2025:
                if kategoria in ['L', 'M1', 'N1'] or kategoria.startswith('L'):
                    koef_eko = 0.50
            else:
                koef_eko = 0.50
        
        result['koeficient_eko'] = koef_eko
        result['sadzba_po_eko'] = result['sadzba_po_veku'] * koef_eko
        
        # Kombinovaná doprava -50%
        if vozidlo.kombi_doprava:
            result['sadzba_finalna'] = result['sadzba_po_eko'] * 0.50
        else:
            result['sadzba_finalna'] = result['sadzba_po_eko']
        
        dan = (result['sadzba_finalna'] / 12) * mesiacov_pouzitia
        result['dan'] = round(dan, 2)
        return result
    
    def vypocitaj_dan_pre_vozidlo(self, vozidlo: Vozidlo) -> Vozidlo:
        mesiacov = vozidlo.pocet_mesiacov_1
        if mesiacov == 0:
            if vozidlo.datum_vzniku_povinnosti:
                try:
                    parts = vozidlo.datum_vzniku_povinnosti.split('.')
                    mesiacov = 13 - int(parts[1])
                except:
                    mesiacov = 12
            else:
                mesiacov = 12
        
        vypocet = self.vypocitaj_dan(vozidlo, mesiacov)
        
        vozidlo.sadzba = int(vypocet['zakladna_sadzba'])
        vozidlo.rocna_sadzba_1 = round(vypocet['sadzba_po_veku'], 2)
        vozidlo.sadzba_po_znizeni_1 = round(vypocet['sadzba_po_eko'], 2)
        vozidlo.sadzba_kombi_1 = round(vypocet['sadzba_finalna'], 2)
        vozidlo.pocet_mesiacov_1 = mesiacov
        vozidlo.dan_1 = vypocet['dan']
        vozidlo.r22 = vypocet['dan']
        
        percento = vypocet['zvysenie_percento']
        if self.rok >= 2025:
            if percento >= 50: vozidlo.zvysenie_1_50 = True
            elif percento >= 40: vozidlo.zvysenie_1_40 = True
            elif percento >= 30: vozidlo.zvysenie_1_30 = True
            elif percento >= 20: vozidlo.zvysenie_1_20 = True
            elif percento >= 10: vozidlo.zvysenie_1_10 = True
        
        return vozidlo


# =============================================================================
# ORSR / REGISTER ÚČTOVNÝCH ZÁVIEROK INTEGRÁCIA
# =============================================================================

class RegisterConnector:
    """Konektor pre získanie údajov z RPO (Register právnických osôb) a RÚZ."""
    
    # RPO API - Štatistický úrad SR
    RPO_API_URL = "https://api.statistics.sk/rpo/v1"
    # RÚZ API - Register účtovných závierok  
    RUZ_API_URL = "https://www.registeruz.sk/cruz-public/api"
    TIMEOUT = 15
    
    def __init__(self):
        self.cache = {}
    
    def _http_get_json(self, url: str, params: dict = None) -> Optional[dict]:
        try:
            if params:
                url = f"{url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url, headers={
                'User-Agent': 'DMVProcessor/2.0',
                'Accept': 'application/json',
            })
            with urllib.request.urlopen(req, timeout=self.TIMEOUT) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception as e:
            print(f"HTTP/JSON chyba pre {url}: {e}")
            return None
    
    def vyhladaj_v_rpo_podla_ico(self, ico: str) -> Optional[Dict[str, Any]]:
        """Vyhľadá subjekt v RPO (Register právnických osôb) podľa IČO."""
        ico = re.sub(r'\D', '', ico).zfill(8)
        
        cache_key = f"rpo_{ico}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # 1. Vyhľadanie podľa IČO
        search_data = self._http_get_json(f"{self.RPO_API_URL}/search", {'identifier': ico})
        if not search_data:
            return None
        
        # Získaj ID organizácie
        org_id = None
        if isinstance(search_data, list) and len(search_data) > 0:
            org_id = search_data[0].get('id')
        elif isinstance(search_data, dict):
            if 'id' in search_data:
                org_id = search_data['id']
            elif 'organizations' in search_data and len(search_data['organizations']) > 0:
                org_id = search_data['organizations'][0].get('id')
        
        if not org_id:
            print(f"RPO: IČO {ico} nenájdené")
            return None
        
        # 2. Získaj detail organizácie
        detail = self._http_get_json(f"{self.RPO_API_URL}/organizations/{org_id}")
        if not detail:
            return None
        
        # Parsuj údaje
        result = {
            'ico': ico,
            'dic': '',
            'nazov': '',
            'ulica': '',
            'cislo': '',
            'psc': '',
            'obec': '',
            'stat': 'Slovenská republika',
        }
        
        # Názov
        if 'name' in detail:
            result['nazov'] = detail['name']
        elif 'names' in detail and len(detail['names']) > 0:
            for name_entry in detail['names']:
                if name_entry.get('effectiveTo') is None:  # Aktuálny názov
                    result['nazov'] = name_entry.get('value', '')
                    break
            if not result['nazov']:
                result['nazov'] = detail['names'][0].get('value', '')
        
        # Sídlo
        if 'addresses' in detail:
            for addr in detail['addresses']:
                if addr.get('effectiveTo') is None:  # Aktuálna adresa
                    result['ulica'] = addr.get('street', '') or addr.get('streetName', '')
                    result['cislo'] = addr.get('buildingNumber', '') or addr.get('regNumber', '')
                    result['psc'] = addr.get('postalCode', '') or ''
                    result['obec'] = addr.get('municipality', '') or addr.get('city', '')
                    break
        elif 'address' in detail:
            addr = detail['address']
            result['ulica'] = addr.get('street', '') or addr.get('streetName', '')
            result['cislo'] = addr.get('buildingNumber', '') or addr.get('regNumber', '')
            result['psc'] = addr.get('postalCode', '') or ''
            result['obec'] = addr.get('municipality', '') or addr.get('city', '')
        
        # DIČ - hľadaj v identifikátoroch
        if 'identifiers' in detail:
            for ident in detail['identifiers']:
                if ident.get('type') == 'DIC' or 'dic' in ident.get('type', '').lower():
                    result['dic'] = ident.get('value', '')
                    break
        
        if result['nazov']:
            self.cache[cache_key] = result
            print(f"RPO: Nájdené - {result['nazov']}")
        
        return result if result['nazov'] else None
    
    def vyhladaj_v_ruz_podla_ico(self, ico: str) -> Optional[Dict[str, Any]]:
        """Vyhľadá subjekt v Registri účtovných závierok podľa IČO."""
        ico = re.sub(r'\D', '', ico).zfill(8)
        
        cache_key = f"ruz_{ico}"
        if cache_key in self.cache:
            return self.cache[cache_key]
        
        # 1. Najprv získaj ID účtovnej jednotky
        search_data = self._http_get_json(f"{self.RUZ_API_URL}/uctovne-jednotky", {
            'zmenene-od': '2000-01-01',
            'pokracovat-za-id': '0',
            'max-zaznamov': '1',
            'ico': ico
        })
        
        if not search_data or 'id' not in search_data:
            return None
        
        uctj_ids = search_data.get('id', [])
        if not uctj_ids or (isinstance(uctj_ids, list) and len(uctj_ids) == 0):
            return None
        
        uctj_id = uctj_ids[0] if isinstance(uctj_ids, list) else uctj_ids
        
        # 2. Získaj detail účtovnej jednotky
        detail = self._http_get_json(f"{self.RUZ_API_URL}/uctovna-jednotka", {'id': uctj_id})
        if not detail:
            return None
        
        result = {
            'ico': ico,
            'dic': detail.get('dic', ''),
            'nazov': detail.get('nazovUJ', ''),
            'ulica': detail.get('ulica', ''),
            'cislo': '',  # RÚZ má ulicu aj s číslom
            'psc': detail.get('psc', ''),
            'obec': detail.get('mesto', ''),
            'stat': 'Slovenská republika',
        }
        
        # Skús oddeliť číslo od ulice
        if result['ulica']:
            match = re.match(r'^(.+?)\s+(\d+[A-Za-z]?(?:/\d+[A-Za-z]?)?)$', result['ulica'])
            if match:
                result['ulica'] = match.group(1)
                result['cislo'] = match.group(2)
        
        if result['nazov']:
            self.cache[cache_key] = result
            print(f"RÚZ: Nájdené - {result['nazov']}")
        
        return result if result['nazov'] else None
    
    def vyhladaj_v_orsr_podla_ico(self, ico: str) -> Optional[Dict[str, Any]]:
        """Vyhľadá subjekt v ORSR podľa IČO (záložná metóda)."""
        # Ponechané pre kompatibilitu, ale RPO/RÚZ sú preferované
        return None
    
    def over_a_doplni_spolocnost(self, spolocnost: Spolocnost) -> Tuple[Spolocnost, bool]:
        """Overí a doplní údaje spoločnosti z verejných registrov."""
        ico = None
        
        if spolocnost.dic:
            dic_clean = re.sub(r'\D', '', spolocnost.dic)
            if len(dic_clean) >= 8:
                if dic_clean.startswith('20'):
                    ico = dic_clean[2:10]
                else:
                    ico = dic_clean[:8]
        
        if not ico:
            print("Nepodarilo sa získať IČO")
            return spolocnost, False
        
        print(f"Overujem spoločnosť s IČO: {ico}")
        
        # Skús najprv RÚZ (má DIČ a kompletnú adresu)
        data = self.vyhladaj_v_ruz_podla_ico(ico)
        source = "RÚZ"
        
        # Ak nie, skús RPO
        if not data:
            data = self.vyhladaj_v_rpo_podla_ico(ico)
            source = "RPO"
        
        if not data:
            print("Spoločnosť nenájdená")
            return spolocnost, False
        
        print(f"Nájdená v {source}: {data.get('nazov', 'N/A')}")
        
        if data.get('nazov'):
            nazov = data['nazov']
            spolocnost.po_obchodne_meno = [nazov[i:i+40] for i in range(0, len(nazov), 40)][:4] if len(nazov) > 40 else [nazov]
        
        if data.get('dic'):
            spolocnost.dic = data['dic']
        if data.get('ulica'):
            spolocnost.sidlo.ulica = data['ulica']
        if data.get('cislo'):
            spolocnost.sidlo.cislo = data['cislo']
        if data.get('psc'):
            spolocnost.sidlo.psc = data['psc']
        if data.get('obec'):
            spolocnost.sidlo.obec = data['obec']
        
        return spolocnost, True


@dataclass
class DanovePriznanie:
    """Kompletné daňové priznanie k dani z motorových vozidiel."""
    # Typ priznania
    rdp: bool = True   # Riadne daňové priznanie
    odp: bool = False  # Opravné daňové priznanie
    ddp: bool = False  # Dodatočné daňové priznanie
    
    # Zdaňovacie obdobie
    obdobie_od: str = ""
    obdobie_do: str = ""
    datum_ddp: str = ""  # Dátum posledného podania (pre DDP)
    
    # Obdobie podľa § 9 (špeciálne prípady)
    par9_ods1: bool = False
    par9_ods3: bool = False
    par9_ods4: bool = False
    par9_ods5: bool = False
    par9_ods6: bool = False
    par9_ods7: bool = False
    
    # Daňovník
    spolocnost: Spolocnost = field(default_factory=Spolocnost)
    
    # Zástupca
    typ_zastupcu_zastupca: bool = False
    typ_zastupcu_dedic: bool = False
    typ_zastupcu_spravca: bool = False
    typ_zastupcu_likvidator: bool = False
    typ_zastupcu_statutar: bool = False
    typ_zastupcu_pravny_nastupca: bool = False
    
    # Údaje zástupcu
    zastupca_priezvisko: str = ""
    zastupca_meno: str = ""
    zastupca_titul: str = ""
    zastupca_titul_za: str = ""
    zastupca_rc: str = ""
    zastupca_datum_narodenia: str = ""
    zastupca_dic: str = ""
    zastupca_obchodne_meno: str = ""
    zastupca_adresa: Adresa = field(default_factory=Adresa)
    
    # Vozidlá
    vozidla: List[Vozidlo] = field(default_factory=list)
    
    # Súhrnné údaje (telo)
    r35_pocet_vozidiel: int = 0
    r36_dan_spolu: float = 0.0
    r37_oslobodenie: float = 0.0
    r38_dan_po_oslobodeni: float = 0.0
    r39_zaplatene_preddavky: float = 0.0
    r40_dan_na_uhradu: float = 0.0
    r41_preddavky_stvrrocne: int = 0
    r42_preddavky_mesacne: int = 0
    r43_suma_stvrrocne: float = 0.0
    r44_suma_mesacne: float = 0.0
    r45_preplatok: int = 0
    
    # Vrátenie preplatku
    vratit_preplatok: bool = False
    sposob_platby_poukazka: bool = False
    sposob_platby_ucet: bool = False
    iban: str = ""
    datum_vratenia: str = ""
    
    # Poznámky a dátum vyhlásenia
    poznamky: str = ""
    datum_vyhlasenia: str = ""


# =============================================================================
# DATABÁZA
# =============================================================================

class Database:
    """SQLite databáza pre ukladanie spoločností a vozidiel."""
    
    def __init__(self, db_path: str = "dmv_database.db"):
        self.db_path = db_path
        self._init_db()
    
    def _init_db(self):
        """Inicializácia databázových tabuliek."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Tabuľka spoločností
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS spolocnosti (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dic TEXT UNIQUE NOT NULL,
                fo INTEGER DEFAULT 0,
                po INTEGER DEFAULT 1,
                zahranicna INTEGER DEFAULT 0,
                datum_narodenia TEXT,
                fo_priezvisko TEXT,
                fo_meno TEXT,
                fo_titul TEXT,
                fo_titul_za TEXT,
                fo_obchodne_meno TEXT,
                po_obchodne_meno TEXT,
                sidlo_ulica TEXT,
                sidlo_cislo TEXT,
                sidlo_psc TEXT,
                sidlo_obec TEXT,
                sidlo_stat TEXT DEFAULT 'Slovenská republika',
                sidlo_telefon TEXT,
                sidlo_email TEXT,
                org_ulica TEXT,
                org_cislo TEXT,
                org_psc TEXT,
                org_obec TEXT,
                org_telefon TEXT,
                org_email TEXT,
                vytvorene TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                aktualizovane TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        # Tabuľka vozidiel
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vozidla (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spolocnost_id INTEGER NOT NULL,
                evc TEXT NOT NULL,
                kategoria TEXT,
                objem_valcov REAL,
                vykon_motora REAL,
                hmotnost REAL,
                pocet_naprav INTEGER,
                datum_prvej_evidencie TEXT,
                hybrid INTEGER DEFAULT 0,
                plyn INTEGER DEFAULT 0,
                vodik INTEGER DEFAULT 0,
                vytvorene TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                aktualizovane TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (spolocnost_id) REFERENCES spolocnosti(id),
                UNIQUE(spolocnost_id, evc)
            )
        """)
        
        # Tabuľka daňových priznaní
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS danove_priznania (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                spolocnost_id INTEGER NOT NULL,
                rok INTEGER NOT NULL,
                typ TEXT DEFAULT 'RDP',
                obdobie_od TEXT,
                obdobie_do TEXT,
                dan_spolu REAL,
                xml_subor TEXT,
                vytvorene TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (spolocnost_id) REFERENCES spolocnosti(id)
            )
        """)
        
        conn.commit()
        conn.close()
    
    def uloz_spolocnost(self, spolocnost: Spolocnost) -> int:
        """Uloží alebo aktualizuje spoločnosť v databáze."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        po_meno = json.dumps(spolocnost.po_obchodne_meno, ensure_ascii=False)
        
        cursor.execute("""
            INSERT INTO spolocnosti (
                dic, fo, po, zahranicna, datum_narodenia,
                fo_priezvisko, fo_meno, fo_titul, fo_titul_za, fo_obchodne_meno,
                po_obchodne_meno,
                sidlo_ulica, sidlo_cislo, sidlo_psc, sidlo_obec, sidlo_stat,
                sidlo_telefon, sidlo_email,
                org_ulica, org_cislo, org_psc, org_obec, org_telefon, org_email
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(dic) DO UPDATE SET
                fo = excluded.fo,
                po = excluded.po,
                zahranicna = excluded.zahranicna,
                datum_narodenia = excluded.datum_narodenia,
                fo_priezvisko = excluded.fo_priezvisko,
                fo_meno = excluded.fo_meno,
                fo_titul = excluded.fo_titul,
                fo_titul_za = excluded.fo_titul_za,
                fo_obchodne_meno = excluded.fo_obchodne_meno,
                po_obchodne_meno = excluded.po_obchodne_meno,
                sidlo_ulica = excluded.sidlo_ulica,
                sidlo_cislo = excluded.sidlo_cislo,
                sidlo_psc = excluded.sidlo_psc,
                sidlo_obec = excluded.sidlo_obec,
                sidlo_stat = excluded.sidlo_stat,
                sidlo_telefon = excluded.sidlo_telefon,
                sidlo_email = excluded.sidlo_email,
                org_ulica = excluded.org_ulica,
                org_cislo = excluded.org_cislo,
                org_psc = excluded.org_psc,
                org_obec = excluded.org_obec,
                org_telefon = excluded.org_telefon,
                org_email = excluded.org_email,
                aktualizovane = CURRENT_TIMESTAMP
        """, (
            spolocnost.dic, int(spolocnost.fo), int(spolocnost.po), int(spolocnost.zahranicna),
            spolocnost.datum_narodenia,
            spolocnost.fo_priezvisko, spolocnost.fo_meno, spolocnost.fo_titul,
            spolocnost.fo_titul_za, spolocnost.fo_obchodne_meno,
            po_meno,
            spolocnost.sidlo.ulica, spolocnost.sidlo.cislo, spolocnost.sidlo.psc,
            spolocnost.sidlo.obec, spolocnost.sidlo.stat,
            spolocnost.sidlo.telefon, spolocnost.sidlo.email_fax,
            spolocnost.adresa_org_zlozky.ulica, spolocnost.adresa_org_zlozky.cislo,
            spolocnost.adresa_org_zlozky.psc, spolocnost.adresa_org_zlozky.obec,
            spolocnost.adresa_org_zlozky.telefon, spolocnost.adresa_org_zlozky.email_fax
        ))
        
        conn.commit()
        spolocnost_id = cursor.lastrowid or self.najdi_spolocnost_podla_dic(spolocnost.dic).id
        conn.close()
        
        return spolocnost_id
    
    def uloz_vozidlo(self, vozidlo: Vozidlo, spolocnost_id: int) -> int:
        """Uloží alebo aktualizuje vozidlo v databáze."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            INSERT INTO vozidla (
                spolocnost_id, evc, kategoria, objem_valcov, vykon_motora,
                hmotnost, pocet_naprav, datum_prvej_evidencie,
                hybrid, plyn, vodik
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(spolocnost_id, evc) DO UPDATE SET
                kategoria = excluded.kategoria,
                objem_valcov = excluded.objem_valcov,
                vykon_motora = excluded.vykon_motora,
                hmotnost = excluded.hmotnost,
                pocet_naprav = excluded.pocet_naprav,
                datum_prvej_evidencie = excluded.datum_prvej_evidencie,
                hybrid = excluded.hybrid,
                plyn = excluded.plyn,
                vodik = excluded.vodik,
                aktualizovane = CURRENT_TIMESTAMP
        """, (
            spolocnost_id, vozidlo.evc, vozidlo.kategoria,
            vozidlo.objem_valcov, vozidlo.vykon_motora,
            vozidlo.hmotnost, vozidlo.pocet_naprav,
            vozidlo.datum_prvej_evidencie,
            int(vozidlo.hybrid), int(vozidlo.plyn), int(vozidlo.vodik)
        ))
        
        conn.commit()
        vozidlo_id = cursor.lastrowid
        conn.close()
        
        return vozidlo_id
    
    def najdi_spolocnost_podla_dic(self, dic: str) -> Optional[Spolocnost]:
        """Nájde spoločnosť podľa DIČ."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM spolocnosti WHERE dic = ?", (dic,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return None
        
        spolocnost = Spolocnost(
            id=row[0],
            dic=row[1],
            fo=bool(row[2]),
            po=bool(row[3]),
            zahranicna=bool(row[4]),
            datum_narodenia=row[5] or "",
            fo_priezvisko=row[6] or "",
            fo_meno=row[7] or "",
            fo_titul=row[8] or "",
            fo_titul_za=row[9] or "",
            fo_obchodne_meno=row[10] or "",
            po_obchodne_meno=json.loads(row[11]) if row[11] else [],
            sidlo=Adresa(
                ulica=row[12] or "",
                cislo=row[13] or "",
                psc=row[14] or "",
                obec=row[15] or "",
                stat=row[16] or "Slovenská republika",
                telefon=row[17] or "",
                email_fax=row[18] or ""
            ),
            adresa_org_zlozky=Adresa(
                ulica=row[19] or "",
                cislo=row[20] or "",
                psc=row[21] or "",
                obec=row[22] or "",
                telefon=row[23] or "",
                email_fax=row[24] or ""
            )
        )
        
        return spolocnost
    
    def najdi_vozidla_spolocnosti(self, spolocnost_id: int) -> List[Vozidlo]:
        """Nájde všetky vozidlá spoločnosti."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM vozidla WHERE spolocnost_id = ?", (spolocnost_id,))
        rows = cursor.fetchall()
        conn.close()
        
        vozidla = []
        for row in rows:
            vozidlo = Vozidlo(
                id=row[0],
                spolocnost_id=row[1],
                evc=row[2] or "",
                kategoria=row[3] or "",
                objem_valcov=row[4] or 0.0,
                vykon_motora=row[5] or 0.0,
                hmotnost=row[6] or 0.0,
                pocet_naprav=row[7] or 0,
                datum_prvej_evidencie=row[8] or "",
                hybrid=bool(row[9]),
                plyn=bool(row[10]),
                vodik=bool(row[11])
            )
            vozidla.append(vozidlo)
        
        return vozidla
    
    def zoznam_spolocnosti(self) -> List[Spolocnost]:
        """Vráti zoznam všetkých spoločností."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        cursor.execute("SELECT dic FROM spolocnosti ORDER BY po_obchodne_meno")
        rows = cursor.fetchall()
        conn.close()
        
        return [self.najdi_spolocnost_podla_dic(row[0]) for row in rows]


# =============================================================================
# PDF EXTRACTOR
# =============================================================================

class PDFExtractor:
    """Extraktor údajov z PDF dokumentov."""
    
    def __init__(self):
        self.patterns = {
            'dic': r'DIČ[:\s]*(\d{10})',
            'ico': r'IČO[:\s]*(\d{8})',
            'evc': r'(?:EČV|EČ|ŠPZ)[:\s]*([A-Z]{2}\s*\d{3}[A-Z]{2})',
            'kategoria': r'(?:Kategória|Druh)[:\s]*(L\d?|M[123]|N[123]|O[1234])',
            'objem': r'(?:Objem|Zdvihový objem)[:\s]*(\d+(?:[,\.]\d+)?)\s*(?:cm³|ccm|cm3)',
            'vykon': r'(?:Výkon|Výkon motora)[:\s]*(\d+(?:[,\.]\d+)?)\s*(?:kW)',
            'hmotnost': r'(?:Hmotnosť|Celková hmotnosť|Najväčšia prípustná hmotnosť)[:\s]*(\d+(?:[,\.]\d+)?)\s*(?:kg)',
            'datum': r'(\d{1,2}[\.\/]\d{1,2}[\.\/]\d{4})',
            'nazov_spolocnosti': r'(?:Obchodné meno|Názov)[:\s]*(.+?)(?:\n|DIČ|IČO)',
            'ulica': r'(?:Ulica|Adresa)[:\s]*(.+?)(?:\n|\d{3}\s*\d{2})',
            'psc_obec': r'(\d{3}\s*\d{2})\s+(.+?)(?:\n|$)',
        }
    
    def extrahuj_text_z_pdf(self, pdf_path: str) -> str:
        """Extrahuje text z PDF súboru."""
        text = ""
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n"
        except Exception as e:
            print(f"Chyba pri extrakcii textu z PDF: {e}")
        
        # Ak sa nepodarilo extrahovať text, skús OCR
        if not text.strip() and OCR_AVAILABLE:
            text = self._extrahuj_text_ocr(pdf_path)
        
        return text
    
    def _extrahuj_text_ocr(self, pdf_path: str) -> str:
        """Extrahuje text z PDF pomocou OCR."""
        text = ""
        
        try:
            images = convert_from_path(pdf_path)
            for i, image in enumerate(images):
                page_text = pytesseract.image_to_string(image, lang='slk+ces')
                text += f"--- Strana {i+1} ---\n{page_text}\n"
        except Exception as e:
            print(f"Chyba pri OCR: {e}")
        
        return text
    
    def extrahuj_tabulky_z_pdf(self, pdf_path: str) -> List[List[List[str]]]:
        """Extrahuje tabuľky z PDF súboru."""
        tabulky = []
        
        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages:
                    page_tables = page.extract_tables()
                    if page_tables:
                        tabulky.extend(page_tables)
        except Exception as e:
            print(f"Chyba pri extrakcii tabuliek z PDF: {e}")
        
        return tabulky
    
    def parsuj_spolocnost(self, text: str) -> Spolocnost:
        """Parsuje údaje o spoločnosti z textu."""
        spolocnost = Spolocnost()
        
        # DIČ
        dic_match = re.search(self.patterns['dic'], text, re.IGNORECASE)
        if dic_match:
            spolocnost.dic = dic_match.group(1)
        
        # Názov spoločnosti
        nazov_match = re.search(self.patterns['nazov_spolocnosti'], text, re.IGNORECASE | re.DOTALL)
        if nazov_match:
            nazov = nazov_match.group(1).strip()
            # Rozdeľ na riadky ak je príliš dlhý
            if len(nazov) > 40:
                casti = [nazov[i:i+40] for i in range(0, len(nazov), 40)]
                spolocnost.po_obchodne_meno = casti[:4]
            else:
                spolocnost.po_obchodne_meno = [nazov]
        
        # Adresa
        ulica_match = re.search(self.patterns['ulica'], text, re.IGNORECASE)
        if ulica_match:
            spolocnost.sidlo.ulica = ulica_match.group(1).strip()
        
        psc_obec_match = re.search(self.patterns['psc_obec'], text)
        if psc_obec_match:
            spolocnost.sidlo.psc = psc_obec_match.group(1).replace(' ', '')
            spolocnost.sidlo.obec = psc_obec_match.group(2).strip()
        
        return spolocnost
    
    def parsuj_vozidlo(self, text: str) -> Vozidlo:
        """Parsuje údaje o vozidle z textu."""
        vozidlo = Vozidlo()
        
        # EČV
        evc_match = re.search(self.patterns['evc'], text, re.IGNORECASE)
        if evc_match:
            vozidlo.evc = evc_match.group(1).replace(' ', '')
        
        # Kategória
        kat_match = re.search(self.patterns['kategoria'], text, re.IGNORECASE)
        if kat_match:
            vozidlo.kategoria = kat_match.group(1).upper()
        
        # Objem valcov
        objem_match = re.search(self.patterns['objem'], text, re.IGNORECASE)
        if objem_match:
            vozidlo.objem_valcov = float(objem_match.group(1).replace(',', '.'))
        
        # Výkon motora
        vykon_match = re.search(self.patterns['vykon'], text, re.IGNORECASE)
        if vykon_match:
            vozidlo.vykon_motora = float(vykon_match.group(1).replace(',', '.'))
        
        # Hmotnosť
        hmotnost_match = re.search(self.patterns['hmotnost'], text, re.IGNORECASE)
        if hmotnost_match:
            vozidlo.hmotnost = float(hmotnost_match.group(1).replace(',', '.'))
        
        # Dátum prvej evidencie
        datumy = re.findall(self.patterns['datum'], text)
        if datumy:
            vozidlo.datum_prvej_evidencie = datumy[0].replace('/', '.')
        
        # Detekcia alternatívneho pohonu
        text_lower = text.lower()
        if 'hybrid' in text_lower:
            vozidlo.hybrid = True
        if any(x in text_lower for x in ['lpg', 'cng', 'plyn']):
            vozidlo.plyn = True
        if 'vodík' in text_lower or 'h2' in text_lower:
            vozidlo.vodik = True
        
        return vozidlo
    
    def parsuj_vozidla_z_tabulky(self, tabulky: List[List[List[str]]]) -> List[Vozidlo]:
        """Parsuje vozidlá z tabuľkových údajov."""
        vozidla = []
        
        for tabulka in tabulky:
            if not tabulka or len(tabulka) < 2:
                continue
            
            # Nájdi hlavičku
            hlavicka = [str(h).lower() if h else '' for h in tabulka[0]]
            
            # Mapovanie stĺpcov
            col_map = {}
            for i, h in enumerate(hlavicka):
                if 'eč' in h or 'spz' in h or 'evidenčn' in h:
                    col_map['evc'] = i
                elif 'kategór' in h or 'druh' in h:
                    col_map['kategoria'] = i
                elif 'objem' in h or 'cm³' in h:
                    col_map['objem'] = i
                elif 'výkon' in h or 'kw' in h:
                    col_map['vykon'] = i
                elif 'hmotno' in h or 'kg' in h:
                    col_map['hmotnost'] = i
                elif 'náprav' in h:
                    col_map['napravy'] = i
            
            # Parsuj riadky
            for riadok in tabulka[1:]:
                if not any(riadok):
                    continue
                
                vozidlo = Vozidlo()
                
                if 'evc' in col_map and col_map['evc'] < len(riadok):
                    vozidlo.evc = str(riadok[col_map['evc']] or '').replace(' ', '')
                
                if 'kategoria' in col_map and col_map['kategoria'] < len(riadok):
                    vozidlo.kategoria = str(riadok[col_map['kategoria']] or '').upper()
                
                if 'objem' in col_map and col_map['objem'] < len(riadok):
                    try:
                        vozidlo.objem_valcov = float(str(riadok[col_map['objem']] or '0').replace(',', '.'))
                    except ValueError:
                        pass
                
                if 'vykon' in col_map and col_map['vykon'] < len(riadok):
                    try:
                        vozidlo.vykon_motora = float(str(riadok[col_map['vykon']] or '0').replace(',', '.'))
                    except ValueError:
                        pass
                
                if 'hmotnost' in col_map and col_map['hmotnost'] < len(riadok):
                    try:
                        vozidlo.hmotnost = float(str(riadok[col_map['hmotnost']] or '0').replace(',', '.'))
                    except ValueError:
                        pass
                
                if 'napravy' in col_map and col_map['napravy'] < len(riadok):
                    try:
                        vozidlo.pocet_naprav = int(str(riadok[col_map['napravy']] or '0'))
                    except ValueError:
                        pass
                
                if vozidlo.evc:
                    vozidla.append(vozidlo)
        
        return vozidla


# =============================================================================
# XML GENERATOR
# =============================================================================

class XMLGenerator:
    """Generátor XML súborov pre finančnú správu SR."""
    
    def __init__(self):
        self.ns = "http://www.financnasprava.sk/form/dmv/2025"
        self.nsmap = {None: self.ns, 'xsi': 'http://www.w3.org/2001/XMLSchema-instance'}
    
    def _bool_to_str(self, value: bool) -> str:
        """Konvertuje bool na '0' alebo '1'."""
        return '1' if value else '0'
    
    def _num_to_str(self, value: float, allow_empty: bool = True) -> str:
        """Konvertuje číslo na string."""
        if allow_empty and (value == 0 or value is None):
            return ''
        return str(value)
    
    def _int_to_str(self, value: int, allow_empty: bool = True) -> str:
        """Konvertuje int na string."""
        if allow_empty and (value == 0 or value is None):
            return ''
        return str(value)
    
    def generuj_xml(self, priznanie: DanovePriznanie) -> str:
        """Generuje kompletný XML súbor pre daňové priznanie."""
        
        # Koreňový element
        dokument = etree.Element('dokument')
        
        # Hlavička
        hlavicka = etree.SubElement(dokument, 'hlavicka')
        
        # Typ osoby
        etree.SubElement(hlavicka, 'fo').text = self._bool_to_str(priznanie.spolocnost.fo)
        etree.SubElement(hlavicka, 'po').text = self._bool_to_str(priznanie.spolocnost.po)
        etree.SubElement(hlavicka, 'zahranicna').text = self._bool_to_str(priznanie.spolocnost.zahranicna)
        
        # Identifikácia
        etree.SubElement(hlavicka, 'dic').text = priznanie.spolocnost.dic
        etree.SubElement(hlavicka, 'datumNarodenia').text = priznanie.spolocnost.datum_narodenia
        
        # Typ daňového priznania
        typ_dp = etree.SubElement(hlavicka, 'typDP')
        etree.SubElement(typ_dp, 'rdp').text = self._bool_to_str(priznanie.rdp)
        etree.SubElement(typ_dp, 'odp').text = self._bool_to_str(priznanie.odp)
        etree.SubElement(typ_dp, 'ddp').text = self._bool_to_str(priznanie.ddp)
        
        # Zdaňovacie obdobie
        obdobie = etree.SubElement(hlavicka, 'zdanovacieObdobie')
        etree.SubElement(obdobie, 'od').text = priznanie.obdobie_od
        etree.SubElement(obdobie, 'do').text = priznanie.obdobie_do
        etree.SubElement(obdobie, 'datumDDP').text = priznanie.datum_ddp
        
        # Obdobie podľa § 9
        par9 = etree.SubElement(hlavicka, 'ObdobiePar9')
        etree.SubElement(par9, 'ods1').text = self._bool_to_str(priznanie.par9_ods1)
        etree.SubElement(par9, 'ods3').text = self._bool_to_str(priznanie.par9_ods3)
        etree.SubElement(par9, 'ods4').text = self._bool_to_str(priznanie.par9_ods4)
        etree.SubElement(par9, 'ods5').text = self._bool_to_str(priznanie.par9_ods5)
        etree.SubElement(par9, 'ods6').text = self._bool_to_str(priznanie.par9_ods6)
        etree.SubElement(par9, 'ods7').text = self._bool_to_str(priznanie.par9_ods7)
        
        # Údaje FO
        etree.SubElement(hlavicka, 'foPriezvisko').text = priznanie.spolocnost.fo_priezvisko
        etree.SubElement(hlavicka, 'foMeno').text = priznanie.spolocnost.fo_meno
        etree.SubElement(hlavicka, 'foTitul').text = priznanie.spolocnost.fo_titul
        etree.SubElement(hlavicka, 'foTitulZa').text = priznanie.spolocnost.fo_titul_za
        etree.SubElement(hlavicka, 'foObchodneMeno').text = priznanie.spolocnost.fo_obchodne_meno
        
        # Obchodné meno PO
        po_meno = etree.SubElement(hlavicka, 'poObchodneMeno')
        mena = priznanie.spolocnost.po_obchodne_meno or ['']
        for i in range(4):
            riadok = etree.SubElement(po_meno, 'riadok')
            riadok.text = mena[i] if i < len(mena) else ''
        
        # Sídlo
        sidlo = etree.SubElement(hlavicka, 'sidlo')
        etree.SubElement(sidlo, 'ulica').text = priznanie.spolocnost.sidlo.ulica
        etree.SubElement(sidlo, 'cislo').text = priznanie.spolocnost.sidlo.cislo
        etree.SubElement(sidlo, 'psc').text = priznanie.spolocnost.sidlo.psc
        etree.SubElement(sidlo, 'obec').text = priznanie.spolocnost.sidlo.obec
        etree.SubElement(sidlo, 'stat').text = priznanie.spolocnost.sidlo.stat
        etree.SubElement(sidlo, 'telefon').text = priznanie.spolocnost.sidlo.telefon
        etree.SubElement(sidlo, 'emailFax').text = priznanie.spolocnost.sidlo.email_fax
        
        # Adresa organizačnej zložky
        org = etree.SubElement(hlavicka, 'adresaOrganizacnejZlozky')
        etree.SubElement(org, 'ulica').text = priznanie.spolocnost.adresa_org_zlozky.ulica
        etree.SubElement(org, 'cislo').text = priznanie.spolocnost.adresa_org_zlozky.cislo
        etree.SubElement(org, 'psc').text = priznanie.spolocnost.adresa_org_zlozky.psc
        etree.SubElement(org, 'obec').text = priznanie.spolocnost.adresa_org_zlozky.obec
        etree.SubElement(org, 'telefon').text = priznanie.spolocnost.adresa_org_zlozky.telefon
        etree.SubElement(org, 'emailFax').text = priznanie.spolocnost.adresa_org_zlozky.email_fax
        
        # Typ zástupcu
        typ_zast = etree.SubElement(hlavicka, 'typZastupcu')
        etree.SubElement(typ_zast, 'typZastupca').text = self._bool_to_str(priznanie.typ_zastupcu_zastupca)
        etree.SubElement(typ_zast, 'dedic').text = self._bool_to_str(priznanie.typ_zastupcu_dedic)
        etree.SubElement(typ_zast, 'spravcaVkonkurznomKonani').text = self._bool_to_str(priznanie.typ_zastupcu_spravca)
        etree.SubElement(typ_zast, 'likvidator').text = self._bool_to_str(priznanie.typ_zastupcu_likvidator)
        etree.SubElement(typ_zast, 'statutarnyZastupcaPO').text = self._bool_to_str(priznanie.typ_zastupcu_statutar)
        etree.SubElement(typ_zast, 'pravnyNastupca').text = self._bool_to_str(priznanie.typ_zastupcu_pravny_nastupca)
        
        # Zástupca
        zastupca = etree.SubElement(hlavicka, 'zastupca')
        etree.SubElement(zastupca, 'priezvisko').text = priznanie.zastupca_priezvisko
        etree.SubElement(zastupca, 'meno').text = priznanie.zastupca_meno
        etree.SubElement(zastupca, 'titul').text = priznanie.zastupca_titul
        etree.SubElement(zastupca, 'titulZa').text = priznanie.zastupca_titul_za
        etree.SubElement(zastupca, 'rc').text = priznanie.zastupca_rc
        etree.SubElement(zastupca, 'datumNarodenia').text = priznanie.zastupca_datum_narodenia
        etree.SubElement(zastupca, 'dic').text = priznanie.zastupca_dic
        etree.SubElement(zastupca, 'obchodneMeno').text = priznanie.zastupca_obchodne_meno
        
        # Adresa zástupcu
        zast_adr = etree.SubElement(zastupca, 'adresa')
        etree.SubElement(zast_adr, 'ulica').text = priznanie.zastupca_adresa.ulica
        etree.SubElement(zast_adr, 'cislo').text = priznanie.zastupca_adresa.cislo
        etree.SubElement(zast_adr, 'psc').text = priznanie.zastupca_adresa.psc
        etree.SubElement(zast_adr, 'obec').text = priznanie.zastupca_adresa.obec
        etree.SubElement(zast_adr, 'stat').text = priznanie.zastupca_adresa.stat
        etree.SubElement(zast_adr, 'telefon').text = priznanie.zastupca_adresa.telefon
        etree.SubElement(zast_adr, 'emailFax').text = priznanie.zastupca_adresa.email_fax
        
        # Telo dokumentu
        telo = etree.SubElement(dokument, 'telo')
        
        # Súhrnné údaje
        etree.SubElement(telo, 'r35').text = self._int_to_str(priznanie.r35_pocet_vozidiel)
        etree.SubElement(telo, 'r36').text = self._num_to_str(priznanie.r36_dan_spolu)
        etree.SubElement(telo, 'r37').text = self._num_to_str(priznanie.r37_oslobodenie)
        etree.SubElement(telo, 'r38').text = self._num_to_str(priznanie.r38_dan_po_oslobodeni)
        etree.SubElement(telo, 'r39').text = self._num_to_str(priznanie.r39_zaplatene_preddavky)
        etree.SubElement(telo, 'r40').text = self._num_to_str(priznanie.r40_dan_na_uhradu)
        etree.SubElement(telo, 'r41').text = self._int_to_str(priznanie.r41_preddavky_stvrrocne)
        etree.SubElement(telo, 'r42').text = self._int_to_str(priznanie.r42_preddavky_mesacne)
        etree.SubElement(telo, 'r43').text = self._num_to_str(priznanie.r43_suma_stvrrocne)
        etree.SubElement(telo, 'r44').text = self._num_to_str(priznanie.r44_suma_mesacne)
        etree.SubElement(telo, 'r45').text = self._int_to_str(priznanie.r45_preplatok)
        
        # Vrátenie preplatku
        vrat = etree.SubElement(telo, 'vrateniePreplatku')
        etree.SubElement(vrat, 'vratit').text = self._bool_to_str(priznanie.vratit_preplatok)
        sposob = etree.SubElement(vrat, 'sposobPlatby')
        etree.SubElement(sposob, 'poukazka').text = self._bool_to_str(priznanie.sposob_platby_poukazka)
        etree.SubElement(sposob, 'ucet').text = self._bool_to_str(priznanie.sposob_platby_ucet)
        etree.SubElement(vrat, 'IBAN').text = priznanie.iban
        etree.SubElement(vrat, 'datum').text = priznanie.datum_vratenia
        
        # Poznámky a dátum vyhlásenia
        etree.SubElement(telo, 'poznamky').text = priznanie.poznamky
        etree.SubElement(telo, 'datumVyhlasenia').text = priznanie.datum_vyhlasenia
        
        # Strany s vozidlami (strana3)
        # Každá strana obsahuje 2 vozidlá (stĺpec1 a stĺpec2)
        celkovy_pocet = len(priznanie.vozidla)
        pocet_stran = (celkovy_pocet + 1) // 2  # Zaokrúhli nahor
        
        if pocet_stran == 0:
            pocet_stran = 1  # Minimálne 1 strana
        
        for i in range(pocet_stran):
            strana = etree.SubElement(telo, 'strana3')
            
            # Označenie strany
            oznacenie = etree.SubElement(strana, 'oznacenie')
            etree.SubElement(oznacenie, 'aktualna').text = str(i + 1)
            etree.SubElement(oznacenie, 'celkovo').text = str(pocet_stran)
            
            # Stĺpec 1 - vozidlo na pozícii 2*i
            vozidlo1_idx = 2 * i
            vozidlo1 = priznanie.vozidla[vozidlo1_idx] if vozidlo1_idx < len(priznanie.vozidla) else None
            stlpec1 = self._generuj_stlpec_vozidla(vozidlo1)
            strana.append(stlpec1)
            stlpec1.tag = 'stlpec1'
            
            # Stĺpec 2 - vozidlo na pozícii 2*i + 1
            vozidlo2_idx = 2 * i + 1
            vozidlo2 = priznanie.vozidla[vozidlo2_idx] if vozidlo2_idx < len(priznanie.vozidla) else None
            stlpec2 = self._generuj_stlpec_vozidla(vozidlo2)
            strana.append(stlpec2)
            stlpec2.tag = 'stlpec2'
        
        # Generuj XML string
        xml_str = etree.tostring(
            dokument,
            encoding='unicode',
            pretty_print=True
        )
        
        # Pridaj XML deklaráciu
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        
        return xml_declaration + xml_str
    
    def _generuj_stlpec_vozidla(self, vozidlo: Optional[Vozidlo]) -> etree.Element:
        """Generuje XML element pre stĺpec vozidla."""
        stlpec = etree.Element('stlpec')
        
        if vozidlo is None:
            vozidlo = Vozidlo()  # Prázdne vozidlo
        
        etree.SubElement(stlpec, 'r01').text = vozidlo.datum_prvej_evidencie
        etree.SubElement(stlpec, 'r02vzniku').text = vozidlo.datum_vzniku_povinnosti
        etree.SubElement(stlpec, 'r02zaniku').text = vozidlo.datum_zaniku_povinnosti
        etree.SubElement(stlpec, 'r03Kategoria').text = vozidlo.kategoria
        etree.SubElement(stlpec, 'r04KodDruhuBA-BB').text = self._bool_to_str(vozidlo.kod_druhu_ba_bb)
        etree.SubElement(stlpec, 'r04KodDruhuBC-BD').text = self._bool_to_str(vozidlo.kod_druhu_bc_bd)
        etree.SubElement(stlpec, 'r05VzduchovePruzenie').text = self._bool_to_str(vozidlo.vzduchove_pruzenie)
        etree.SubElement(stlpec, 'r05IneSystemy').text = self._bool_to_str(vozidlo.ine_systemy)
        etree.SubElement(stlpec, 'r06-EVC').text = vozidlo.evc
        etree.SubElement(stlpec, 'r07-ObjemValcov').text = self._num_to_str(vozidlo.objem_valcov)
        etree.SubElement(stlpec, 'r08-VykonMotora').text = self._num_to_str(vozidlo.vykon_motora)
        etree.SubElement(stlpec, 'r09Hmotnost').text = self._num_to_str(vozidlo.hmotnost)
        etree.SubElement(stlpec, 'r10PocetNaprav').text = self._int_to_str(vozidlo.pocet_naprav)
        etree.SubElement(stlpec, 'r11pism').text = vozidlo.r11_pismeno
        etree.SubElement(stlpec, 'r12pism').text = vozidlo.r12_pismeno
        etree.SubElement(stlpec, 'r12oslobodene').text = self._bool_to_str(vozidlo.r12_oslobodene)
        etree.SubElement(stlpec, 'r13sadzba').text = self._int_to_str(vozidlo.sadzba)
        
        # Zvýšenie/zníženie sadzby - stĺpec 1
        etree.SubElement(stlpec, 'r14zvysenieSadzby1_10').text = self._bool_to_str(vozidlo.zvysenie_1_10)
        etree.SubElement(stlpec, 'r14zvysenieSadzby1_20').text = self._bool_to_str(vozidlo.zvysenie_1_20)
        etree.SubElement(stlpec, 'r14zvysenieSadzby1_30').text = self._bool_to_str(vozidlo.zvysenie_1_30)
        etree.SubElement(stlpec, 'r14zvysenieSadzby1_40').text = self._bool_to_str(vozidlo.zvysenie_1_40)
        etree.SubElement(stlpec, 'r14zvysenieSadzby1_50').text = self._bool_to_str(vozidlo.zvysenie_1_50)
        
        # Zvýšenie/zníženie sadzby - stĺpec 2
        etree.SubElement(stlpec, 'r14zvysenieSadzby2_10').text = self._bool_to_str(vozidlo.zvysenie_2_10)
        etree.SubElement(stlpec, 'r14zvysenieSadzby2_20').text = self._bool_to_str(vozidlo.zvysenie_2_20)
        etree.SubElement(stlpec, 'r14zvysenieSadzby2_30').text = self._bool_to_str(vozidlo.zvysenie_2_30)
        etree.SubElement(stlpec, 'r14zvysenieSadzby2_40').text = self._bool_to_str(vozidlo.zvysenie_2_40)
        etree.SubElement(stlpec, 'r14zvysenieSadzby2_50').text = self._bool_to_str(vozidlo.zvysenie_2_50)
        
        # Ročné sadzby
        etree.SubElement(stlpec, 'r15rocnaSadzba_1').text = self._num_to_str(vozidlo.rocna_sadzba_1)
        etree.SubElement(stlpec, 'r15rocnaSadzba_2').text = self._num_to_str(vozidlo.rocna_sadzba_2)
        
        # Ekologické zníženie
        etree.SubElement(stlpec, 'r16hybrid').text = self._bool_to_str(vozidlo.hybrid)
        etree.SubElement(stlpec, 'r16plyn').text = self._bool_to_str(vozidlo.plyn)
        etree.SubElement(stlpec, 'r16vodik').text = self._bool_to_str(vozidlo.vodik)
        
        # Sadzby po znížení
        etree.SubElement(stlpec, 'r17sadzba1').text = self._num_to_str(vozidlo.sadzba_po_znizeni_1)
        etree.SubElement(stlpec, 'r17sadzba2').text = self._num_to_str(vozidlo.sadzba_po_znizeni_2)
        
        # Kombinovaná doprava
        etree.SubElement(stlpec, 'r18KombiDoprava').text = self._bool_to_str(vozidlo.kombi_doprava)
        
        # Sadzby pre kombi
        etree.SubElement(stlpec, 'r19sadzba1').text = self._num_to_str(vozidlo.sadzba_kombi_1)
        etree.SubElement(stlpec, 'r19sadzba2').text = self._num_to_str(vozidlo.sadzba_kombi_2)
        
        # Počet mesiacov a dní
        etree.SubElement(stlpec, 'r20aPocMesS1').text = self._int_to_str(vozidlo.pocet_mesiacov_1)
        etree.SubElement(stlpec, 'r20aPocMesS2').text = self._int_to_str(vozidlo.pocet_mesiacov_2)
        etree.SubElement(stlpec, 'r20bPocDniS1').text = self._int_to_str(vozidlo.pocet_dni_1)
        etree.SubElement(stlpec, 'r20bPocDniS2').text = self._int_to_str(vozidlo.pocet_dni_2)
        
        # Daň
        etree.SubElement(stlpec, 'r21dan1').text = self._num_to_str(vozidlo.dan_1)
        etree.SubElement(stlpec, 'r21dan2').text = self._num_to_str(vozidlo.dan_2)
        
        # Sumarizácia
        etree.SubElement(stlpec, 'r22').text = self._num_to_str(vozidlo.r22)
        etree.SubElement(stlpec, 'r23').text = self._num_to_str(vozidlo.r23)
        etree.SubElement(stlpec, 'r24').text = self._num_to_str(vozidlo.r24)
        etree.SubElement(stlpec, 'r25').text = self._num_to_str(vozidlo.r25)
        
        return stlpec
    
    def validuj_xml(self, xml_str: str, xsd_path: str) -> tuple[bool, str]:
        """Validuje XML oproti XSD schéme."""
        try:
            xsd_doc = etree.parse(xsd_path)
            xsd_schema = etree.XMLSchema(xsd_doc)
            
            xml_doc = etree.fromstring(xml_str.encode('utf-8'))
            
            if xsd_schema.validate(xml_doc):
                return True, "XML je validné"
            else:
                errors = [str(e) for e in xsd_schema.error_log]
                return False, "\n".join(errors)
        except Exception as e:
            return False, f"Chyba pri validácii: {str(e)}"


# =============================================================================
# HLAVNÁ APLIKÁCIA
# =============================================================================

class DMVProcessor:
    """Hlavná trieda pre spracovanie dane z motorových vozidiel."""
    
    def __init__(self, db_path: str = "dmv_database.db"):
        self.db = Database(db_path)
        self.extractor = PDFExtractor()
        self.generator = XMLGenerator()
        self.register = RegisterConnector()
        self.kalkulator = None  # Inicializuje sa podľa roku
    
    def spracuj_pdf(self, pdf_path: str, over_v_registri: bool = True) -> tuple[Spolocnost, List[Vozidlo]]:
        """
        Spracuje PDF súbor a extrahuje údaje o spoločnosti a vozidlách.
        
        Args:
            pdf_path: Cesta k PDF súboru
            over_v_registri: Či overiť údaje v ORSR/RÚZ
            
        Returns:
            Tuple (spoločnosť, zoznam vozidiel)
        """
        print(f"Spracúvam PDF: {pdf_path}")
        
        # Extrahuj text
        text = self.extractor.extrahuj_text_z_pdf(pdf_path)
        print(f"Extrahovaný text ({len(text)} znakov)")
        
        # Extrahuj tabuľky
        tabulky = self.extractor.extrahuj_tabulky_z_pdf(pdf_path)
        print(f"Nájdených tabuliek: {len(tabulky)}")
        
        # Parsuj spoločnosť
        spolocnost = self.extractor.parsuj_spolocnost(text)
        
        # Overenie a doplnenie z registrov
        if over_v_registri and spolocnost.dic:
            print("Overujem údaje v registroch...")
            spolocnost, uspech = self.register.over_a_doplni_spolocnost(spolocnost)
            if uspech:
                print("✓ Údaje overené a doplnené")
            else:
                print("⚠ Údaje sa nepodarilo overiť")
        
        # Parsuj vozidlá z textu
        vozidla_text = [self.extractor.parsuj_vozidlo(text)]
        
        # Parsuj vozidlá z tabuliek
        vozidla_tabulky = self.extractor.parsuj_vozidla_z_tabulky(tabulky)
        
        # Kombinuj vozidlá (preferuj tabuľkové, ak existujú)
        if vozidla_tabulky:
            vozidla = vozidla_tabulky
        else:
            vozidla = [v for v in vozidla_text if v.evc]
        
        print(f"Nájdená spoločnosť: {spolocnost.po_obchodne_meno}")
        print(f"Nájdených vozidiel: {len(vozidla)}")
        
        return spolocnost, vozidla
    
    def over_spolocnost(self, dic_alebo_ico: str) -> Optional[Spolocnost]:
        """
        Overí a získa údaje o spoločnosti z registrov.
        
        Args:
            dic_alebo_ico: DIČ alebo IČO spoločnosti
            
        Returns:
            Objekt spoločnosti alebo None
        """
        spolocnost = Spolocnost(dic=dic_alebo_ico)
        spolocnost, uspech = self.register.over_a_doplni_spolocnost(spolocnost)
        return spolocnost if uspech else None
    
    def vypocitaj_dane(self, vozidla: List[Vozidlo], rok: int = None) -> List[Vozidlo]:
        """
        Vypočíta dane pre všetky vozidlá.
        
        Args:
            vozidla: Zoznam vozidiel
            rok: Zdaňovacie obdobie
            
        Returns:
            Zoznam vozidiel s vypočítanými daňami
        """
        if rok is None:
            rok = datetime.now().year - 1
        
        self.kalkulator = KalkulatorDane(rok)
        
        for vozidlo in vozidla:
            self.kalkulator.vypocitaj_dan_pre_vozidlo(vozidlo)
            print(f"  {vozidlo.evc}: {vozidlo.kategoria} -> {vozidlo.dan_1:.2f} EUR")
        
        return vozidla
    
    def vytvor_priznanie(
        self,
        spolocnost: Spolocnost,
        vozidla: List[Vozidlo],
        rok: int = None,
        typ: str = 'RDP',
        vypocitaj_dane: bool = True
    ) -> DanovePriznanie:
        """
        Vytvorí daňové priznanie z údajov.
        
        Args:
            spolocnost: Údaje o spoločnosti
            vozidla: Zoznam vozidiel
            rok: Zdaňovacie obdobie (rok), default aktuálny rok - 1
            typ: Typ priznania (RDP, ODP, DDP)
            vypocitaj_dane: Či automaticky vypočítať dane
            
        Returns:
            Kompletné daňové priznanie
        """
        if rok is None:
            rok = datetime.now().year - 1
        
        # Vypočítaj dane ak je požadované
        if vypocitaj_dane:
            print(f"\nVypočítavam dane za rok {rok}:")
            vozidla = self.vypocitaj_dane(vozidla, rok)
        
        priznanie = DanovePriznanie()
        
        # Typ priznania
        priznanie.rdp = typ == 'RDP'
        priznanie.odp = typ == 'ODP'
        priznanie.ddp = typ == 'DDP'
        
        # Zdaňovacie obdobie
        priznanie.obdobie_od = f"1.1.{rok}"
        priznanie.obdobie_do = f"31.12.{rok}"
        
        # Spoločnosť
        priznanie.spolocnost = spolocnost
        
        # Vozidlá
        priznanie.vozidla = vozidla
        
        # Súhrnné údaje
        priznanie.r35_pocet_vozidiel = len(vozidla)
        
        # Vypočítaj súhrnné dane
        dan_spolu = sum(v.r22 for v in vozidla)
        oslobodenie = sum(v.r23 for v in vozidla)
        
        priznanie.r36_dan_spolu = dan_spolu
        priznanie.r37_oslobodenie = oslobodenie
        priznanie.r38_dan_po_oslobodeni = dan_spolu - oslobodenie
        priznanie.r40_dan_na_uhradu = priznanie.r38_dan_po_oslobodeni - priznanie.r39_zaplatene_preddavky
        
        # Dátum vyhlásenia
        priznanie.datum_vyhlasenia = datetime.now().strftime("%d.%m.%Y")
        
        return priznanie
    
    def generuj_xml_subor(
        self,
        priznanie: DanovePriznanie,
        output_path: str = None
    ) -> str:
        """
        Generuje XML súbor pre finančnú správu.
        
        Args:
            priznanie: Daňové priznanie
            output_path: Cesta pre výstupný súbor (voliteľné)
            
        Returns:
            Cesta k vygenerovanému súboru
        """
        # Generuj XML
        xml_content = self.generator.generuj_xml(priznanie)
        
        # Určí názov súboru
        if output_path is None:
            dic = priznanie.spolocnost.dic or "neznamy"
            rok = priznanie.obdobie_od.split('.')[-1] if priznanie.obdobie_od else datetime.now().year
            output_path = f"dmv_{dic}_{rok}.xml"
        
        # Ulož súbor
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml_content)
        
        print(f"XML súbor vytvorený: {output_path}")
        
        return output_path
    
    def uloz_do_databazy(self, spolocnost: Spolocnost, vozidla: List[Vozidlo]) -> int:
        """
        Uloží spoločnosť a vozidlá do databázy.
        
        Args:
            spolocnost: Údaje o spoločnosti
            vozidla: Zoznam vozidiel
            
        Returns:
            ID spoločnosti v databáze
        """
        # Ulož spoločnosť
        spolocnost_id = self.db.uloz_spolocnost(spolocnost)
        print(f"Spoločnosť uložená s ID: {spolocnost_id}")
        
        # Ulož vozidlá
        for vozidlo in vozidla:
            vozidlo_id = self.db.uloz_vozidlo(vozidlo, spolocnost_id)
            print(f"Vozidlo {vozidlo.evc} uložené s ID: {vozidlo_id}")
        
        return spolocnost_id
    
    def spracuj_kompletne(
        self,
        pdf_path: str,
        output_xml: str = None,
        rok: int = None
    ) -> str:
        """
        Kompletné spracovanie: PDF -> Databáza -> XML
        
        Args:
            pdf_path: Cesta k PDF súboru
            output_xml: Cesta pre výstupný XML (voliteľné)
            rok: Zdaňovacie obdobie
            
        Returns:
            Cesta k vygenerovanému XML súboru
        """
        # 1. Spracuj PDF
        spolocnost, vozidla = self.spracuj_pdf(pdf_path)
        
        # 2. Ulož do databázy
        self.uloz_do_databazy(spolocnost, vozidla)
        
        # 3. Vytvor priznanie
        priznanie = self.vytvor_priznanie(spolocnost, vozidla, rok)
        
        # 4. Generuj XML
        xml_path = self.generuj_xml_subor(priznanie, output_xml)
        
        return xml_path


# =============================================================================
# CLI ROZHRANIE
# =============================================================================

def main():
    """Hlavná funkcia pre CLI použitie."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='DMV Processor v2.0 - Spracovanie dane z motorových vozidiel SR',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Príklady použitia:
  python dmv_processor.py spracuj dokument.pdf -r 2024
  python dmv_processor.py over 2020123456
  python dmv_processor.py vypocet -k M1 -o 1998 -d 15.3.2020 -r 2024
  python dmv_processor.py demo -o demo.xml -r 2024
        """
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Príkazy')
    
    # Príkaz: spracuj
    spracuj_parser = subparsers.add_parser('spracuj', help='Spracuj PDF a vytvor XML')
    spracuj_parser.add_argument('pdf', help='Cesta k PDF súboru')
    spracuj_parser.add_argument('-o', '--output', help='Výstupný XML súbor')
    spracuj_parser.add_argument('-r', '--rok', type=int, help='Zdaňovacie obdobie (rok)')
    spracuj_parser.add_argument('-d', '--db', default='dmv_database.db', help='Cesta k databáze')
    spracuj_parser.add_argument('--bez-overenia', action='store_true', help='Preskočiť overenie v ORSR/RÚZ')
    
    # Príkaz: over (NOVÉ)
    over_parser = subparsers.add_parser('over', help='Overí spoločnosť v ORSR / RÚZ')
    over_parser.add_argument('ico_dic', help='IČO alebo DIČ spoločnosti')
    
    # Príkaz: vypocet (NOVÉ)
    vypocet_parser = subparsers.add_parser('vypocet', help='Vypočíta daň pre vozidlo')
    vypocet_parser.add_argument('-k', '--kategoria', default='M1', help='Kategória (L, M1, N1, O1-O4)')
    vypocet_parser.add_argument('-o', '--objem', type=float, default=0, help='Objem valcov cm³')
    vypocet_parser.add_argument('-m', '--hmotnost', type=float, default=0, help='Hmotnosť v kg')
    vypocet_parser.add_argument('-n', '--napravy', type=int, default=2, help='Počet náprav')
    vypocet_parser.add_argument('-d', '--datum', help='Dátum prvej evidencie (dd.mm.yyyy)')
    vypocet_parser.add_argument('-r', '--rok', type=int, help='Zdaňovacie obdobie')
    vypocet_parser.add_argument('--hybrid', action='store_true', help='Hybridné vozidlo')
    vypocet_parser.add_argument('--plyn', action='store_true', help='Vozidlo na CNG/LPG')
    vypocet_parser.add_argument('--mesiacov', type=int, default=12, help='Počet mesiacov')
    
    # Príkaz: zoznam
    zoznam_parser = subparsers.add_parser('zoznam', help='Zobraz zoznam spoločností')
    zoznam_parser.add_argument('-d', '--db', default='dmv_database.db', help='Cesta k databáze')
    
    # Príkaz: export
    export_parser = subparsers.add_parser('export', help='Exportuj XML pre existujúcu spoločnosť')
    export_parser.add_argument('dic', help='DIČ spoločnosti')
    export_parser.add_argument('-o', '--output', help='Výstupný XML súbor')
    export_parser.add_argument('-r', '--rok', type=int, help='Zdaňovacie obdobie (rok)')
    export_parser.add_argument('-d', '--db', default='dmv_database.db', help='Cesta k databáze')
    
    # Príkaz: demo
    demo_parser = subparsers.add_parser('demo', help='Vytvor demo XML s ukážkovými údajmi')
    demo_parser.add_argument('-o', '--output', default='demo_dmv.xml', help='Výstupný XML súbor')
    demo_parser.add_argument('-r', '--rok', type=int, default=2024, help='Zdaňovacie obdobie')
    
    args = parser.parse_args()
    
    if args.command == 'spracuj':
        processor = DMVProcessor(args.db)
        spolocnost, vozidla = processor.spracuj_pdf(args.pdf, not getattr(args, 'bez_overenia', False))
        processor.uloz_do_databazy(spolocnost, vozidla)
        priznanie = processor.vytvor_priznanie(spolocnost, vozidla, args.rok)
        xml_path = processor.generuj_xml_subor(priznanie, args.output)
        print(f"\n✓ Hotovo! XML súbor: {xml_path}")
    
    elif args.command == 'over':
        print(f"\n{'='*60}")
        print(f"Overenie spoločnosti: {args.ico_dic}")
        print('='*60)
        
        connector = RegisterConnector()
        ico = re.sub(r'\D', '', args.ico_dic)
        
        if len(ico) == 10 and ico.startswith('20'):
            ico = ico[2:]
        
        print("\n[1/2] Hľadám v Registri účtovných závierok...")
        data = connector.vyhladaj_v_ruz_podla_ico(ico)
        if data:
            print(f"  ✓ Nájdené!")
        else:
            print(f"  ✗ Nenájdené")
        
        print("[2/2] Hľadám v Obchodnom registri SR...")
        data_orsr = connector.vyhladaj_v_orsr_podla_ico(ico)
        if data_orsr:
            print(f"  ✓ Nájdené!")
            if not data:
                data = data_orsr
        else:
            print(f"  ✗ Nenájdené")
        
        if data:
            print(f"\n{'─'*60}")
            print(f"Názov:    {data.get('nazov', 'N/A')}")
            print(f"IČO:      {data.get('ico', 'N/A')}")
            print(f"DIČ:      {data.get('dic', 'N/A')}")
            print(f"Adresa:   {data.get('ulica', '')} {data.get('cislo', '')}")
            print(f"          {data.get('psc', '')} {data.get('obec', '')}")
            print('─'*60)
        else:
            print("\n⚠ Spoločnosť nebola nájdená v žiadnom registri.")
    
    elif args.command == 'vypocet':
        rok = args.rok or (datetime.now().year - 1)
        print(f"\n{'='*60}")
        print(f"Výpočet dane z motorového vozidla za rok {rok}")
        print('='*60)
        
        vozidlo = Vozidlo(
            kategoria=args.kategoria.upper(),
            objem_valcov=args.objem,
            hmotnost=args.hmotnost,
            pocet_naprav=args.napravy,
            datum_prvej_evidencie=args.datum or "",
            hybrid=args.hybrid,
            plyn=args.plyn,
        )
        
        kalkulator = KalkulatorDane(rok)
        vypocet = kalkulator.vypocitaj_dan(vozidlo, args.mesiacov)
        
        print(f"\nVstupné údaje:")
        print(f"  Kategória:           {args.kategoria}")
        if args.objem > 0:
            print(f"  Objem valcov:        {args.objem} cm³")
        if args.hmotnost > 0:
            print(f"  Hmotnosť:            {args.hmotnost} kg")
        if args.datum:
            print(f"  Prvá evidencia:      {args.datum}")
            print(f"  Vek vozidla:         {vypocet['vek_mesiacov']} mesiacov")
        if args.hybrid:
            print(f"  Pohon:               Hybrid (-50%)")
        if args.plyn:
            print(f"  Pohon:               CNG/LPG (-50%)")
        
        print(f"\nVýpočet dane:")
        print(f"  1. Základná sadzba:     {vypocet['zakladna_sadzba']:>8} EUR")
        
        koef = vypocet['koeficient_veku']
        if koef != 1.0:
            zmena = "zníženie" if koef < 1 else "zvýšenie"
            percento = abs(int((koef - 1.0) * 100))
            print(f"  2. Úprava podľa veku ({zmena} {percento}%):")
            print(f"                          {vypocet['sadzba_po_veku']:>8.2f} EUR")
        
        if vypocet['koeficient_eko'] != 1.0:
            print(f"  3. Zníženie eko (-50%): {vypocet['sadzba_po_eko']:>8.2f} EUR")
        
        print(f"\n  Ročná sadzba:           {vypocet['sadzba_finalna']:>8.2f} EUR")
        print(f"  Počet mesiacov:         {args.mesiacov:>8}")
        print(f"  {'─'*38}")
        print(f"  DAŇ ZA ROK {rok}:        {vypocet['dan']:>8.2f} EUR")
        print('='*60)
    
    elif args.command == 'zoznam':
        db = Database(args.db)
        spolocnosti = db.zoznam_spolocnosti()
        
        if not spolocnosti:
            print("Žiadne spoločnosti v databáze.")
        else:
            print("\nZoznam spoločností:")
            print("-" * 60)
            for s in spolocnosti:
                nazov = s.po_obchodne_meno[0] if s.po_obchodne_meno else "Neznámy názov"
                print(f"DIČ: {s.dic:15} | {nazov}")
    
    elif args.command == 'export':
        processor = DMVProcessor(args.db)
        
        spolocnost = processor.db.najdi_spolocnost_podla_dic(args.dic)
        if not spolocnost:
            print(f"Spoločnosť s DIČ {args.dic} nenájdená.")
            return
        
        vozidla = processor.db.najdi_vozidla_spolocnosti(spolocnost.id)
        priznanie = processor.vytvor_priznanie(spolocnost, vozidla, args.rok)
        xml_path = processor.generuj_xml_subor(priznanie, args.output)
        print(f"\n✓ Hotovo! XML súbor: {xml_path}")
    
    elif args.command == 'demo':
        rok = args.rok
        spolocnost = Spolocnost(
            po=True,
            dic="2020123456",
            po_obchodne_meno=["Demo s.r.o."],
            sidlo=Adresa(
                ulica="Hlavná",
                cislo="1",
                psc="81101",
                obec="Bratislava",
                stat="Slovenská republika"
            )
        )
        
        vozidla = [
            Vozidlo(
                evc="BA123AB",
                kategoria="M1",
                objem_valcov=1998,
                datum_prvej_evidencie="15.3.2020",
                datum_vzniku_povinnosti=f"1.1.{rok}",
            ),
            Vozidlo(
                evc="BA456CD",
                kategoria="N1",
                hmotnost=2500,
                datum_prvej_evidencie="1.6.2022",
                datum_vzniku_povinnosti=f"1.1.{rok}",
                hybrid=True
            )
        ]
        
        processor = DMVProcessor()
        priznanie = processor.vytvor_priznanie(spolocnost, vozidla, rok)
        xml_path = processor.generuj_xml_subor(priznanie, args.output)
        print(f"\n✓ Demo XML súbor vytvorený: {xml_path}")
    
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
