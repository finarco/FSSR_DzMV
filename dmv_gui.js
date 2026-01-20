// DMV Processor v2.0 - JavaScript GUI
// ====================================

// Konfigurácia
const API_URL = window.location.origin;
const USE_API = window.location.protocol !== 'file:';

let vozidla = [];
let editIndex = -1;

// Sadzby dane
const SADZBY_M1 = [
    {od: 0, do: 150, sadzba: 50}, {od: 150, do: 900, sadzba: 62},
    {od: 900, do: 1200, sadzba: 80}, {od: 1200, do: 1500, sadzba: 115},
    {od: 1500, do: 2000, sadzba: 148}, {od: 2000, do: 3000, sadzba: 180},
    {od: 3000, do: Infinity, sadzba: 218}
];
const SADZBY_O = {O1: 50, O2: 115, O3: 180, O4: 295};
const SADZBY_N1 = [
    {od: 0, do: 2, sadzba: 115}, {od: 2, do: 4, sadzba: 148},
    {od: 4, do: 6, sadzba: 180}, {od: 6, do: 8, sadzba: 218},
    {od: 8, do: 10, sadzba: 253}, {od: 10, do: 12, sadzba: 295}
];
const UPRAVA_2024 = [
    {od: 0, do: 36, koef: 0.75}, {od: 36, do: 72, koef: 0.80},
    {od: 72, do: 108, koef: 0.85}, {od: 108, do: 144, koef: 1.00},
    {od: 144, do: 156, koef: 1.10}, {od: 156, do: Infinity, koef: 1.20}
];
const UPRAVA_2025 = [
    {od: 0, do: 36, koef: 1.00}, {od: 36, do: 72, koef: 1.10},
    {od: 72, do: 108, koef: 1.20}, {od: 108, do: 144, koef: 1.30},
    {od: 144, do: 180, koef: 1.40}, {od: 180, do: Infinity, koef: 1.50}
];

// === LOG ===
function log(msg) {
    const box = document.getElementById('logBox');
    const time = new Date().toLocaleTimeString('sk-SK', {hour: '2-digit', minute: '2-digit'});
    box.innerHTML += `<div class="log-entry"><span class="time">[${time}]</span> ${msg}</div>`;
    box.scrollTop = box.scrollHeight;
}

// === SPOLOČNOSŤ - OVERENIE ===
async function overitSpolocnost() {
    const dic = document.getElementById('dic').value.trim();
    const ico = document.getElementById('ico').value.trim();
    
    if (!dic && !ico) {
        alert('Zadajte DIČ alebo IČO');
        return;
    }
    
    const btn = document.getElementById('btnOverit');
    const statusEl = document.getElementById('spolocnostStatus');
    
    btn.disabled = true;
    btn.innerHTML = '<span class="loading"></span> Overujem...';
    statusEl.innerHTML = '<span class="status status-warning">Overujem...</span>';
    log('Overujem spoločnosť v registroch...');
    
    try {
        if (USE_API) {
            const response = await fetch(`${API_URL}/api/overit`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ico: ico || dic, dic: dic})
            });
            
            const result = await response.json();
            
            if (result.success) {
                const data = result.data;
                document.getElementById('dic').value = data.dic || dic;
                document.getElementById('ico').value = data.ico || ico;
                document.getElementById('nazov').value = data.nazov || '';
                document.getElementById('ulica').value = data.ulica || '';
                document.getElementById('cislo').value = data.cislo || '';
                document.getElementById('psc').value = data.psc || '';
                document.getElementById('obec').value = data.obec || '';
                
                statusEl.innerHTML = `<span class="status status-success">✓ ${result.source}</span>`;
                log(`Nájdené: ${data.nazov}`);
            } else {
                statusEl.innerHTML = '<span class="status status-error">✗ Nenájdené</span>';
                log('Spoločnosť nenájdená');
                alert(result.error || 'Spoločnosť nenájdená v registroch');
            }
        } else {
            statusEl.innerHTML = '<span class="status status-error">✗ Offline</span>';
            log('Spustite server: python dmv_server.py');
            alert('Pre overenie spustite server:\n\npython dmv_server.py\n\nOtvorte http://localhost:5100');
        }
    } catch (error) {
        statusEl.innerHTML = '<span class="status status-error">✗ Chyba</span>';
        log(`Chyba: ${error.message}`);
        alert(`Chyba: ${error.message}\n\nSpustite server: python dmv_server.py`);
    } finally {
        btn.disabled = false;
        btn.innerHTML = '🔍 Overiť v registroch';
    }
}

// === UPLOAD PDF ===
async function uploadPDF(input) {
    const file = input.files[0];
    if (!file) return;
    
    if (!file.name.toLowerCase().endsWith('.pdf')) {
        alert('Vyberte PDF súbor');
        input.value = '';
        return;
    }
    
    log(`Načítavam: ${file.name}`);
    
    if (!USE_API) {
        alert('Pre načítanie PDF spustite server:\n\npython dmv_server.py\n\nOtvorte http://localhost:5100');
        input.value = '';
        return;
    }
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('rok', document.getElementById('rok').value);
    
    try {
        const response = await fetch(`${API_URL}/api/upload-pdf`, {
            method: 'POST',
            body: formData
        });
        
        const result = await response.json();
        
        if (result.success) {
            const sp = result.spolocnost;
            document.getElementById('dic').value = sp.dic || '';
            document.getElementById('nazov').value = sp.nazov || '';
            document.getElementById('ulica').value = sp.ulica || '';
            document.getElementById('cislo').value = sp.cislo || '';
            document.getElementById('psc').value = sp.psc || '';
            document.getElementById('obec').value = sp.obec || '';
            
            vozidla = result.vozidla || [];
            prepocitatVsetky();
            
            log(result.message);
            alert(`Načítané:\n• ${sp.nazov || '(bez názvu)'}\n• ${vozidla.length} vozidiel`);
        } else {
            log(`Chyba: ${result.error}`);
            alert(`Chyba: ${result.error}`);
        }
    } catch (error) {
        log(`Chyba: ${error.message}`);
        alert(`Chyba: ${error.message}`);
    }
    
    input.value = '';
}

// === VÝPOČTY ===
function getZakladnaSadzba(kat, objem, hmotnost) {
    if (kat === 'M1' || kat === 'L') {
        for (const s of SADZBY_M1) {
            if (objem > s.od && objem <= s.do) return s.sadzba;
        }
        return 218;
    }
    if (kat.startsWith('O')) return SADZBY_O[kat] || 50;
    const hmotnostT = hmotnost / 1000;
    for (const s of SADZBY_N1) {
        if (hmotnostT > s.od && hmotnostT <= s.do) return s.sadzba;
    }
    return 115;
}

function vypocitajVek(datumStr) {
    if (!datumStr) return 0;
    const parts = datumStr.split('.');
    if (parts.length !== 3) return 0;
    const rok = parseInt(document.getElementById('rok').value);
    const datumEv = new Date(parts[2], parts[1] - 1, parts[0]);
    const koniec = new Date(rok, 11, 31);
    return Math.max(0, (koniec.getFullYear() - datumEv.getFullYear()) * 12 + 
                       (koniec.getMonth() - datumEv.getMonth()));
}

function getKoefVeku(vek) {
    const rok = parseInt(document.getElementById('rok').value);
    const upravy = rok >= 2025 ? UPRAVA_2025 : UPRAVA_2024;
    for (const u of upravy) {
        if (vek >= u.od && vek < u.do) return u.koef;
    }
    return upravy[upravy.length - 1].koef;
}

function prepocitat() {
    const kat = document.getElementById('vKategoria').value;
    const objem = parseFloat(document.getElementById('vObjem').value) || 0;
    const hmotnost = parseFloat(document.getElementById('vHmotnost').value) || 0;
    const datum = document.getElementById('vDatum').value;
    const mesiace = parseInt(document.getElementById('vMesiace').value) || 12;
    const hybrid = document.getElementById('vHybrid').checked;
    const plyn = document.getElementById('vPlyn').checked;
    const kombi = document.getElementById('vKombi').checked;
    
    let zakladna = getZakladnaSadzba(kat, objem, hmotnost);
    document.getElementById('calcZakladna').textContent = zakladna + ' €';
    
    const vek = vypocitajVek(datum);
    let sadzba = zakladna;
    
    const vekRow = document.getElementById('calcVekRow');
    if (vek > 0 && !kat.startsWith('O')) {
        const koef = getKoefVeku(vek);
        sadzba = zakladna * koef;
        const perc = Math.round((koef - 1) * 100);
        vekRow.style.display = 'flex';
        document.getElementById('calcVek').textContent = 
            `${perc >= 0 ? '+' : ''}${perc}% (${vek} mes.) = ${sadzba.toFixed(2)} €`;
    } else {
        vekRow.style.display = 'none';
    }
    
    const ekoRow = document.getElementById('calcEkoRow');
    if (hybrid || plyn) {
        sadzba *= 0.5;
        ekoRow.style.display = 'flex';
    } else {
        ekoRow.style.display = 'none';
    }
    
    if (kombi) sadzba *= 0.5;
    
    const dan = (sadzba / 12) * mesiace;
    document.getElementById('calcDan').textContent = dan.toFixed(2) + ' €';
    
    return {zakladna, sadzba, dan, vek};
}

// === VOZIDLÁ ===
function onKatChange() {
    const kat = document.getElementById('vKategoria').value;
    document.getElementById('objemGroup').style.display = (kat === 'M1' || kat === 'L') ? 'flex' : 'none';
    document.getElementById('hmotnostGroup').style.display = (kat === 'N1') ? 'flex' : 'none';
}

function openModal(index = -1) {
    editIndex = index;
    const rok = document.getElementById('rok').value;
    document.getElementById('modalTitle').textContent = index >= 0 ? 'Upraviť vozidlo' : 'Nové vozidlo';
    
    if (index >= 0) {
        const v = vozidla[index];
        document.getElementById('vEvc').value = v.evc || '';
        document.getElementById('vKategoria').value = v.kategoria || 'M1';
        document.getElementById('vDatum').value = v.datum || '';
        document.getElementById('vDatumVzniku').value = v.datumVzniku || `1.1.${rok}`;
        document.getElementById('vObjem').value = v.objem || '';
        document.getElementById('vHmotnost').value = v.hmotnost || '';
        document.getElementById('vMesiace').value = v.mesiace || 12;
        document.getElementById('vHybrid').checked = v.hybrid || false;
        document.getElementById('vPlyn').checked = v.plyn || false;
        document.getElementById('vKombi').checked = v.kombi || false;
    } else {
        document.getElementById('vEvc').value = '';
        document.getElementById('vKategoria').value = 'M1';
        document.getElementById('vDatum').value = '';
        document.getElementById('vDatumVzniku').value = `1.1.${rok}`;
        document.getElementById('vObjem').value = '';
        document.getElementById('vHmotnost').value = '';
        document.getElementById('vMesiace').value = 12;
        document.getElementById('vHybrid').checked = false;
        document.getElementById('vPlyn').checked = false;
        document.getElementById('vKombi').checked = false;
    }
    
    onKatChange();
    prepocitat();
    document.getElementById('vozidloModal').classList.add('active');
}

function closeModal() {
    document.getElementById('vozidloModal').classList.remove('active');
    editIndex = -1;
}

function ulozitVozidlo() {
    const evc = document.getElementById('vEvc').value.toUpperCase().replace(/\s/g, '');
    if (!evc) { alert('Zadajte EČV'); return; }
    
    const vypocet = prepocitat();
    const vozidlo = {
        evc,
        kategoria: document.getElementById('vKategoria').value,
        datum: document.getElementById('vDatum').value,
        datumVzniku: document.getElementById('vDatumVzniku').value,
        objem: parseFloat(document.getElementById('vObjem').value) || 0,
        hmotnost: parseFloat(document.getElementById('vHmotnost').value) || 0,
        mesiace: parseInt(document.getElementById('vMesiace').value) || 12,
        hybrid: document.getElementById('vHybrid').checked,
        plyn: document.getElementById('vPlyn').checked,
        kombi: document.getElementById('vKombi').checked,
        zakladnaSadzba: vypocet.zakladna,
        sadzba: vypocet.sadzba,
        dan: vypocet.dan,
        vek: vypocet.vek
    };
    
    if (editIndex >= 0) {
        vozidla[editIndex] = vozidlo;
        log(`Upravené: ${evc}`);
    } else {
        vozidla.push(vozidlo);
        log(`Pridané: ${evc}`);
    }
    
    refreshTable();
    closeModal();
}

function odstranVozidlo(index) {
    if (confirm('Odstrániť vozidlo?')) {
        log(`Odstránené: ${vozidla[index].evc}`);
        vozidla.splice(index, 1);
        refreshTable();
    }
}

function prepocitatVsetky() {
    vozidla.forEach(v => {
        const zakladna = getZakladnaSadzba(v.kategoria, v.objem, v.hmotnost);
        const vek = vypocitajVek(v.datum);
        let sadzba = zakladna;
        if (vek > 0 && !v.kategoria.startsWith('O')) sadzba = zakladna * getKoefVeku(vek);
        if (v.hybrid || v.plyn) sadzba *= 0.5;
        if (v.kombi) sadzba *= 0.5;
        v.zakladnaSadzba = zakladna;
        v.sadzba = sadzba;
        v.vek = vek;
        v.dan = (sadzba / 12) * v.mesiace;
    });
    refreshTable();
    log('Prepočítané');
}

function refreshTable() {
    const tbody = document.getElementById('vozidlaTable');
    tbody.innerHTML = '';
    let celkom = 0;
    
    vozidla.forEach((v, i) => {
        let zaklad = v.kategoria === 'M1' || v.kategoria === 'L' 
            ? v.objem + ' cm³' 
            : v.kategoria.startsWith('O') ? v.kategoria : v.hmotnost + ' kg';
        
        tbody.innerHTML += `<tr>
            <td><strong>${v.evc}</strong></td>
            <td>${v.kategoria}</td>
            <td>${zaklad}</td>
            <td>${v.vek || '-'}</td>
            <td>${v.sadzba ? v.sadzba.toFixed(2) : '-'}</td>
            <td>${v.mesiace}</td>
            <td><strong>${v.dan ? v.dan.toFixed(2) : '0.00'}</strong></td>
            <td>
                <button class="btn-secondary btn-sm" onclick="openModal(${i})">✏️</button>
                <button class="btn-danger btn-sm" onclick="odstranVozidlo(${i})">🗑️</button>
            </td>
        </tr>`;
        celkom += v.dan || 0;
    });
    
    document.getElementById('celkovaDan').innerHTML = `<strong>${celkom.toFixed(2)}</strong>`;
}

// === EXPORT XML ===
async function exportXML() {
    const dic = document.getElementById('dic').value;
    if (!dic) { alert('Zadajte DIČ'); return; }
    if (vozidla.length === 0) { alert('Pridajte vozidlo'); return; }
    
    const rok = document.getElementById('rok').value;
    
    if (USE_API) {
        // Server-side export
        try {
            const response = await fetch(`${API_URL}/api/export-xml`, {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    spolocnost: {
                        typ: 'PO',
                        dic: dic,
                        nazov: document.getElementById('nazov').value,
                        ulica: document.getElementById('ulica').value,
                        cislo: document.getElementById('cislo').value,
                        psc: document.getElementById('psc').value,
                        obec: document.getElementById('obec').value,
                    },
                    vozidla: vozidla,
                    rok: rok
                })
            });
            
            if (response.ok) {
                const blob = await response.blob();
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `dmv_${dic}_${rok}.xml`;
                a.click();
                URL.revokeObjectURL(url);
                log(`Exportované: dmv_${dic}_${rok}.xml`);
            } else {
                const err = await response.json();
                alert(`Chyba: ${err.error}`);
            }
        } catch (error) {
            alert(`Chyba: ${error.message}`);
        }
    } else {
        // Client-side export (zjednodušený)
        let celkovaDan = vozidla.reduce((sum, v) => sum + (v.dan || 0), 0);
        let xml = `<?xml version="1.0" encoding="UTF-8"?>
<dokument>
<hlavicka>
<fo>0</fo><po>1</po><zahranicna>0</zahranicna>
<dic>${dic}</dic>
<typDP><rdp>1</rdp><odp>0</odp><ddp>0</ddp></typDP>
<zdanovacieObdobie><od>1.1.${rok}</od><do>31.12.${rok}</do></zdanovacieObdobie>
<poObchodneMeno><riadok>${document.getElementById('nazov').value}</riadok></poObchodneMeno>
<sidlo>
<ulica>${document.getElementById('ulica').value}</ulica>
<cislo>${document.getElementById('cislo').value}</cislo>
<psc>${document.getElementById('psc').value}</psc>
<obec>${document.getElementById('obec').value}</obec>
<stat>Slovenská republika</stat>
</sidlo>
</hlavicka>
<telo>
<r35>${vozidla.length}</r35>
<r36>${celkovaDan.toFixed(2)}</r36>
<r38>${celkovaDan.toFixed(2)}</r38>
<r40>${celkovaDan.toFixed(2)}</r40>
</telo>
</dokument>`;
        
        const blob = new Blob([xml], {type: 'application/xml'});
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `dmv_${dic}_${rok}.xml`;
        a.click();
        URL.revokeObjectURL(url);
        log(`Exportované (zjednodušené): dmv_${dic}_${rok}.xml`);
    }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    log('DMV Processor v2.0 pripravený');
    if (!USE_API) {
        log('Offline režim - pre plnú funkcionalitu spustite: python dmv_server.py');
    }
});
