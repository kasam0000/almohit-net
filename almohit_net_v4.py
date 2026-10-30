import getpass
import threading
import time
import sqlite3
from datetime import datetime
from flask import Flask, jsonify, render_template_string, request
import routeros_api

# إعدادات افتراضية
HOST = "172.16.0.1"
PORT = 8728
USER = "admin"
PASS = ""
INTERVAL = 10

app = Flask(__name__)
lock = threading.Lock()

def init_db():
    conn = sqlite3.connect('almohit_modems.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS modems 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, lat REAL, lng REAL, ip TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS settings 
                 (key TEXT PRIMARY KEY, value TEXT)''')
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('network_name', 'شبكة المحيط نت')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('router_ip', '172.16.0.1')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('router_user', 'admin')")
    c.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('router_pass', '')")
    conn.commit()
    conn.close()
init_db()

state = {
    "stats": {"active_users": 0, "total_devices": 0},
    "devices": [],
    "network_name": "شبكة المحيط نت",
    "router_ip": "172.16.0.1",
    "error": "",
    "updated": ""
}

api = None
pool = None
current_host = HOST

def read_resource(path):
    try:
        if api: return api.get_resource(path).get()
        return []
    except: return []

def refresh_data():
    global api, pool, current_host, USER, PASS
    while True:
        if not api:
            try:
                # محاولة الاتصال باستخدام البيانات المحدثة
                pool = routeros_api.RouterOsApiPool(current_host, username=USER, password=PASS, port=PORT, plaintext_login=True)
                api = pool.get_api()
                api.get_resource("/system/identity").get()
            except Exception as e:
                with lock: 
                    # إظهار رسالة خطأ أوضح
                    err_msg = str(e)
                    if "invalid user name or password" in err_msg.lower():
                        state["error"] = "كلمة المرور أو اسم المستخدم خاطئ"
                    elif "timed out" in err_msg.lower() or "timeout" in err_msg.lower():
                        state["error"] = f"انتهت المهلة (تأكد من الـ IP والشبكة)"
                    else:
                        state["error"] = f"فشل الاتصال: {current_host}"
                time.sleep(5)
                continue

        try:
            active = read_resource("/ip/hotspot/active")
            leases = read_resource("/ip/dhcp-server/lease")
            arp = read_resource("/ip/arp")
            wireless = read_resource("/interface/wireless/registration-table")
            if not wireless: wireless = read_resource("/interface/wifi/registration-table")
            
            mac_signal = {}
            for w in wireless:
                mac = w.get("mac-address", "").lower()
                sig = w.get("signal-strength", "0").replace("dBm", "")
                try: sig_val = int(sig)
                except: sig_val = 0
                mac_signal[mac] = sig_val

            devices = {}
            for row in arp + leases + active:
                ip = row.get("address")
                mac = row.get("mac-address", "").lower()
                if ip:
                    devices[ip] = {"ip": ip, "mac": mac, "signal": mac_signal.get(mac, 0)}
            
            with lock:
                state["stats"]["active_users"] = len(active)
                state["stats"]["total_devices"] = len(devices)
                state["devices"] = list(devices.values())
                state["updated"] = datetime.now().strftime("%H:%M:%S")
                state["error"] = ""
        except Exception as e:
            with lock: 
                state["error"] = "خطأ في قراءة البيانات (تم قطع الاتصال)"
                api = None
        time.sleep(INTERVAL)

PAGE = r"""
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
<title>المحيط نت</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;700;900&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root { --bg: #0f172a; --glass: rgba(15, 23, 42, 0.85); --glass-border: rgba(56, 189, 248, 0.4); --primary: #0ea5e9; --text: #f8fafc; }
* { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Cairo', sans-serif; -webkit-tap-highlight-color: transparent; }
body { background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }
#map { position: absolute; top: 0; left: 0; width: 100%; height: 100%; z-index: 1; }
.top-hud { position: absolute; top: 15px; left: 50%; transform: translateX(-50%); width: 94%; max-width: 450px; background: var(--glass); backdrop-filter: blur(10px); border: 1px solid var(--glass-border); border-radius: 16px; z-index: 1000; padding: 12px 15px; box-shadow: 0 8px 32px rgba(0,0,0,0.4); display: flex; flex-direction: column; gap: 10px; }
.hud-header { display: flex; justify-content: space-between; align-items: center; }
.hud-title { font-size: 15px; font-weight: 900; color: #fff; display: flex; align-items: center; gap: 5px; }
.hud-title span { color: #0ea5e9; cursor: pointer; font-size: 14px; }
.connect-btn { background: rgba(14, 165, 233, 0.15); border: 1px solid #0ea5e9; color: #0ea5e9; padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: bold; cursor: pointer; display: flex; align-items: center; gap: 5px; transition: 0.3s; }
.connect-btn:active { background: #0ea5e9; color: white; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: #10b981; box-shadow: 0 0 8px #10b981; }
.status-text { font-size: 11px; color: #94a3b8; }
.hud-stats { display: flex; justify-content: space-between; align-items: center; background: rgba(0,0,0,0.4); border-radius: 10px; padding: 8px; }
.stat-item { text-align: center; display: flex; flex-direction: column; align-items: center; gap: 2px; }
.stat-val { font-size: 14px; font-weight: 700; }
.stat-lbl { font-size: 10px; color: #94a3b8; }
.dot { width: 6px; height: 6px; border-radius: 50%; display: inline-block; margin-left: 4px; }
.fab-btn { position: absolute; bottom: 85px; right: 20px; z-index: 1000; width: 60px; height: 60px; border-radius: 50%; background: linear-gradient(135deg, #0ea5e9, #2563eb); color: white; border: 2px solid #fff; font-size: 28px; display: flex; justify-content: center; align-items: center; box-shadow: 0 4px 15px rgba(37, 99, 235, 0.5); cursor: pointer; transition: transform 0.2s; }
.fab-btn:active { transform: scale(0.9); }
.bottom-nav { position: absolute; bottom: 15px; left: 50%; transform: translateX(-50%); width: 90%; max-width: 400px; background: var(--glass); backdrop-filter: blur(10px); border: 1px solid var(--glass-border); border-radius: 20px; z-index: 1000; display: flex; justify-content: space-around; padding: 8px 10px; }
.nav-item { display: flex; flex-direction: column; align-items: center; font-size: 11px; color: #94a3b8; cursor: pointer; gap: 3px; }
.nav-item.active { color: #0ea5e9; }
.nav-icon { font-size: 18px; }
.refresh-mini { position: absolute; bottom: 85px; left: 20px; z-index: 1000; background: var(--glass); border: 1px solid var(--glass-border); padding: 8px 12px; border-radius: 12px; font-size: 11px; display: flex; flex-direction: column; align-items: center; cursor: pointer; backdrop-filter: blur(10px); color: white; }
.modal { display: none; position: absolute; bottom: 0; left: 0; width: 100%; background: rgba(15, 23, 42, 0.98); backdrop-filter: blur(15px); z-index: 3000; border-top: 2px solid #0ea5e9; border-radius: 20px 20px 0 0; padding: 25px 20px; box-shadow: 0 -10px 30px rgba(0,0,0,0.8); }
.modal-content input { width: 100%; padding: 12px; margin-bottom: 10px; border-radius: 10px; border: 1px solid #334155; background: rgba(0,0,0,0.4); color: white; font-size: 14px; outline: none; transition: 0.3s; }
.modal-content input:focus { border-color: #0ea5e9; box-shadow: 0 0 10px rgba(14, 165, 233, 0.2); }
.btn-action { padding: 12px; width: 48%; border: none; border-radius: 10px; cursor: pointer; font-weight: bold; font-size: 14px; transition: 0.2s; }
.btn-save { background: linear-gradient(135deg, #10b981, #059669); color: white; }
.btn-cancel { background: linear-gradient(135deg, #ef4444, #dc2626); color: white; }
.custom-green-icon { background-color: #10b981; width: 22px; height: 22px; border-radius: 50%; border: 3px solid white; box-shadow: 0 0 10px rgba(16,185,129,0.8); }
</style>
</head>
<body>

<div id="map"></div>

<div class="top-hud">
    <div class="hud-header">
        <div class="hud-title">
            <span id="networkNameDisplay">شبكة المحيط نت</span>
            <span onclick="changeNetworkName()">✏️</span>
        </div>
        <button class="connect-btn" onclick="openSettings()">
            <span>🔗</span> اتصال
        </button>
    </div>
    
    <div style="display:flex; justify-content:space-between; align-items:center; padding:0 5px;">
        <div style="display:flex; align-items:center; gap:5px;">
            <div class="status-dot" id="statusDot"></div>
            <span class="status-text" id="statusText">جارٍ الاتصال...</span>
        </div>
        <span class="status-text" id="routerIpDisplay" dir="ltr">IP: 172.16.0.1</span>
    </div>

    <div class="hud-stats">
        <div class="stat-item"><div class="stat-val" style="color:#0ea5e9;"><span class="dot" style="background:#0ea5e9;"></span><span id="cnt-total">0</span></div><div class="stat-lbl">الإجمالي</div></div>
        <div class="stat-item"><div class="stat-val" style="color:#10b981;"><span class="dot" style="background:#10b981;"></span><span id="cnt-good">0</span></div><div class="stat-lbl">جيد</div></div>
        <div class="stat-item"><div class="stat-val" style="color:#eab308;"><span class="dot" style="background:#eab308;"></span><span id="cnt-weak">0</span></div><div class="stat-lbl">ضعيف</div></div>
        <div class="stat-item"><div class="stat-val" style="color:#ef4444;"><span class="dot" style="background:#ef4444;"></span><span id="cnt-disc">0</span></div><div class="stat-lbl">مفصول</div></div>
        <div class="stat-item"><div class="stat-val" style="color:#94a3b8;"><span class="dot" style="background:#94a3b8;"></span><span id="cnt-nodata">0</span></div><div class="stat-lbl">بلا بيانات</div></div>
    </div>
</div>

<div class="refresh-mini" onclick="forceRefresh()">
    <span>🔄 تحديث</span>
    <span style="font-size:9px; color:#94a3b8; margin-top:2px;" id="lastUpdate">--:--</span>
</div>

<div class="fab-btn" onclick="openAddModal()">+</div>

<div class="bottom-nav">
    <div class="nav-item active" onclick="showMap()">
        <span class="nav-icon">🗺️</span>
        <span>الخريطة</span>
    </div>
    <div class="nav-item" onclick="openStats()">
        <span class="nav-icon">📊</span>
        <span>الإحصائيات</span>
    </div>
</div>

<!-- نافذة إعدادات الاتصال (IP + User + Pass) -->
<div class="modal" id="settingsModal">
    <div class="modal-content">
        <h3 style="margin-bottom:15px; color:#0ea5e9; text-align:center; font-size:18px;">بيانات الاتصال بالشبكة</h3>
        <input type="text" id="routerIp" placeholder="عنوان IP الراوتر" dir="ltr" style="text-align:left;">
        <input type="text" id="routerUser" placeholder="اسم المستخدم (Username)" dir="ltr" style="text-align:left;">
        <input type="password" id="routerPass" placeholder="كلمة المرور (Password)" dir="ltr" style="text-align:left;">
        <div style="display:flex; justify-content:space-between; margin-top:10px;">
            <button class="btn-action btn-save" onclick="changeIp()">🔌 اتصال</button>
            <button class="btn-action btn-cancel" onclick="closeSettings()">✖ إغلاق</button>
        </div>
    </div>
</div>

<!-- نافذة إضافة المودم -->
<div class="modal" id="modemModal">
    <div class="modal-content">
        <h3 id="modalTitle" style="margin-bottom:15px; color:#0ea5e9; text-align:center; font-size:18px;">إضافة مودم جديد</h3>
        <p style="font-size:12px; color:#f59e0b; text-align:center; margin-bottom:15px;">📍 حرك الدائرة الخضراء فوق المنزل بدقة</p>
        <input type="hidden" id="editId">
        <input type="text" id="mName" placeholder="اسم المودم / رقم الكارت">
        <input type="text" id="mIp" placeholder="عنوان IP (اختياري)" dir="ltr" style="text-align:left;">
        <div style="display:flex; justify-content:space-between; margin-top:10px;">
            <button class="btn-action btn-save" onclick="saveModem()">💾 حفظ</button>
            <button class="btn-action btn-cancel" onclick="closeModal()">✖ إلغاء</button>
        </div>
    </div>
</div>

<!-- نافذة الإحصائيات -->
<div class="modal" id="statsModal" style="height: 50%;">
    <div class="modal-content">
        <h3 style="margin-bottom:20px; color:#0ea5e9; text-align:center; font-size:18px;">إحصائيات الشبكة</h3>
        <div style="display:flex; justify-content:space-around; text-align:center;">
            <div>
                <div style="font-size:36px; color:#0ea5e9; font-weight:bold;" id="s-active">0</div>
                <div style="color:#94a3b8; font-size:12px; margin-top:5px;">مستخدمي الهوت سبوت</div>
            </div>
            <div>
                <div style="font-size:36px; color:#10b981; font-weight:bold;" id="s-devices">0</div>
                <div style="color:#94a3b8; font-size:12px; margin-top:5px;">الأجهزة المتصلة</div>
            </div>
        </div>
        <button class="btn-action btn-cancel" style="width:100%; margin-top:30px;" onclick="closeStats()">إغلاق</button>
    </div>
</div>

<script>
const map = L.map('map', { zoomControl: false }).setView([-6.9147, 107.6098], 16);
L.control.zoom({ position: 'topleft' }).addTo(map);
L.tileLayer('https://{s}.google.com/vt/lyrs=s,h&x={x}&y={y}&z={z}', { maxZoom: 20, subdomains:['mt0','mt1','mt2','mt3'] }).addTo(map);

let markersLayer = L.layerGroup().addTo(map);
let tempMarker = null;
let liveDevices = [];
let currentRouterIp = "172.16.0.1";
let currentRouterUser = "admin";
let currentRouterPass = "";

const greenIcon = L.divIcon({ className: 'custom-green-icon', iconSize: [22, 22], iconAnchor: [11, 11] });

function createCircleMarker(lat, lng, color, popupContent) {
    let marker = L.circleMarker([lat, lng], { radius: 8, fillColor: color, color: "#fff", weight: 2, opacity: 1, fillOpacity: 1 }).addTo(markersLayer);
    if(popupContent) marker.bindPopup(popupContent);
    return marker;
}

function openAddModal() {
    let center = map.getCenter();
    if (tempMarker) map.removeLayer(tempMarker);
    tempMarker = L.marker(center, { icon: greenIcon, draggable: true }).addTo(map);
    document.getElementById('modalTitle').innerText = "إضافة مودم جديد";
    document.getElementById('editId').value = "";
    document.getElementById('mName').value = "";
    document.getElementById('mIp').value = "";
    document.getElementById('modemModal').style.display = 'block';
}
function closeModal() {
    document.getElementById('modemModal').style.display = 'none';
    if (tempMarker) { map.removeLayer(tempMarker); tempMarker = null; }
}

function openSettings() { 
    document.getElementById('routerIp').value = currentRouterIp;
    document.getElementById('routerUser').value = currentRouterUser;
    document.getElementById('routerPass').value = currentRouterPass;
    document.getElementById('settingsModal').style.display = 'block'; 
}
function closeSettings() { document.getElementById('settingsModal').style.display = 'none'; }
function openStats() { document.getElementById('statsModal').style.display = 'block'; }
function closeStats() { document.getElementById('statsModal').style.display = 'none'; }
function showMap() { closeStats(); }

function changeIp() {
    let ip = document.getElementById('routerIp').value.trim();
    let user = document.getElementById('routerUser').value.trim();
    let pass = document.getElementById('routerPass').value;
    if(!ip || !user) return alert("الرجاء إدخال الـ IP واسم المستخدم");
    
    fetch('/api/change_ip', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ip: ip, user: user, pass: pass}) })
    .then(r => r.json()).then(data => { 
        alert(data.message); 
        closeSettings(); 
        currentRouterIp = ip;
        currentRouterUser = user;
        currentRouterPass = pass;
    });
}

function changeNetworkName() {
    let newName = prompt("أدخل اسم شبكتك الجديد:", document.getElementById('networkNameDisplay').innerText);
    if (newName) {
        fetch('/api/update_network', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({name: newName}) })
        .then(() => { document.getElementById('networkNameDisplay').innerText = newName; });
    }
}

function forceRefresh() { loadMarkers(); }

function saveModem() {
    let name = document.getElementById('mName').value;
    let ip = document.getElementById('mIp').value;
    let id = document.getElementById('editId').value;
    if (!name) return alert("يرجى إدخال اسم المودم");
    if (!tempMarker) return alert("يرجى تحديد الموقع أولاً");

    let latlng = tempMarker.getLatLng();
    let data = { name: name, ip: ip, lat: latlng.lat, lng: latlng.lng };
    let url = id ? '/api/markers/' + id : '/api/markers';
    let method = id ? 'PUT' : 'POST';

    fetch(url, { method: method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) })
    .then(() => { closeModal(); loadMarkers(); });
}

async function loadMarkers() {
    markersLayer.clearLayers();
    try {
        let res = await fetch('/api/markers');
        let modems = await res.json();
        let dataRes = await fetch('/api/data');
        let liveData = await dataRes.json();
        liveDevices = liveData.devices;

        document.getElementById('networkNameDisplay').innerText = liveData.network_name;
        document.getElementById('lastUpdate').innerText = liveData.updated;
        
        currentRouterIp = liveData.router_ip;
        document.getElementById('routerIpDisplay').innerText = "IP: " + currentRouterIp;

        let dot = document.getElementById('statusDot');
        let txt = document.getElementById('statusText');
        if(liveData.error) { 
            dot.style.background = '#ef4444'; dot.style.boxShadow = '0 0 8px #ef4444'; 
            txt.innerText = liveData.error; // إظهار رسالة الخطأ الحقيقية
            txt.style.color = '#ef4444';
        } else { 
            dot.style.background = '#10b981'; dot.style.boxShadow = '0 0 8px #10b981'; 
            txt.innerText = 'متصل'; 
            txt.style.color = '#10b981';
        }

        let counts = { total: modems.length, good: 0, weak: 0, disc: 0, nodata: 0 };

        modems.forEach(m => {
            let color = "#94a3b8"; 
            let statusText = "لا يوجد IP";
            let signalText = "";
            
            if (m.ip) {
                let liveDev = liveDevices.find(d => d.ip === m.ip);
                if (liveDev) {
                    let sig = liveDev.signal;
                    if (sig < -75 && sig !== 0) {
                        color = "#eab308"; statusText = "إشارة ضعيفة"; signalText = `الإشارة: ${sig} dBm`; counts.weak++;
                    } else {
                        color = "#10b981"; statusText = "متصل (جيد)"; signalText = sig !== 0 ? `الإشارة: ${sig} dBm` : "سلكي"; counts.good++;
                    }
                } else {
                    color = "#ef4444"; statusText = "مفصول (Offline)"; counts.disc++;
                }
            } else { counts.nodata++; }

            let popup = `
                <div style="text-align:center; font-family:'Cairo',sans-serif; min-width:150px; color:#fff;">
                    <b style="font-size:14px; color:#0ea5e9;">${m.name}</b><br>
                    <span dir="ltr" style="font-size:12px;">IP: ${m.ip || 'لا يوجد'}</span><br>
                    <span style="color:${color}; font-weight:bold;">${statusText}</span><br>
                    ${signalText ? `<span style="font-size:11px;">${signalText}</span><br>` : ''}
                    <div style="display:flex; justify-content:space-around; margin-top:10px;">
                        <button onclick="editModem(${m.id}, '${m.name}', '${m.ip}')" style="padding:5px 10px; background:#0ea5e9; color:white; border:none; border-radius:5px; cursor:pointer; font-family:'Cairo';">تعديل</button>
                        <button onclick="deleteModem(${m.id})" style="padding:5px 10px; background:#ef4444; color:white; border:none; border-radius:5px; cursor:pointer; font-family:'Cairo';">حذف</button>
                    </div>
                </div>`;
            
            createCircleMarker(m.lat, m.lng, color, popup);
        });

        document.getElementById('cnt-total').innerText = counts.total;
        document.getElementById('cnt-good').innerText = counts.good;
        document.getElementById('cnt-weak').innerText = counts.weak;
        document.getElementById('cnt-disc').innerText = counts.disc;
        document.getElementById('cnt-nodata').innerText = counts.nodata;

    } catch(e) { console.log(e); }
}

function editModem(id, name, ip) {
    fetch('/api/markers').then(r=>r.json()).then(data => {
        let m = data.find(x => x.id === id);
        if(m) {
            if(tempMarker) map.removeLayer(tempMarker);
            tempMarker = L.marker([m.lat, m.lng], { icon: greenIcon, draggable: true }).addTo(map);
            map.setView([m.lat, m.lng], 17);
            document.getElementById('modalTitle').innerText = "تعديل المودم";
            document.getElementById('editId').value = id;
            document.getElementById('mName').value = name;
            document.getElementById('mIp').value = ip || "";
            document.getElementById('modemModal').style.display = 'block';
        }
    });
    map.closePopup();
}

function deleteModem(id) {
    if (confirm("هل أنت متأكد من حذف هذا المودم؟")) {
        fetch('/api/markers/' + id, { method: 'DELETE' }).then(() => loadMarkers());
    }
}

async function updateStats() {
    try {
        let res = await fetch('/api/data');
        let data = await res.json();
        document.getElementById('s-active').innerText = data.stats.active_users;
        document.getElementById('s-devices').innerText = data.stats.total_devices;
    } catch(e) {}
}

setInterval(updateStats, 5000);
setInterval(loadMarkers, 10000);
updateStats();
loadMarkers();
</script>
</body>
</html>
"""

@app.route("/")
def home(): return render_template_string(PAGE)

@app.route("/api/data")
def get_data():
    with lock: 
        conn = sqlite3.connect('almohit_modems.db')
        c = conn.cursor()
        c.execute("SELECT value FROM settings WHERE key='network_name'"); row = c.fetchone()
        if row: state["network_name"] = row[0]
        c.execute("SELECT value FROM settings WHERE key='router_ip'"); row_ip = c.fetchone()
        if row_ip: state["router_ip"] = row_ip[0]
        conn.close()
        return jsonify(state)

@app.route("/api/update_network", methods=['POST'])
def update_network():
    new_name = request.json.get('name')
    if new_name:
        conn = sqlite3.connect('almohit_modems.db')
        c = conn.cursor()
        c.execute("UPDATE settings SET value=? WHERE key='network_name'", (new_name,))
        conn.commit()
        conn.close()
    return jsonify({"status": "success"})

@app.route("/api/change_ip", methods=['POST'])
def change_ip():
    global current_host, api, pool, USER, PASS
    data = request.json
    new_ip = data.get('ip')
    new_user = data.get('user')
    new_pass = data.get('pass')
    
    if new_ip and new_user:
        current_host = new_ip
        USER = new_user
        PASS = new_pass
        
        if api: 
            try: pool.disconnect()
            except: pass
        api = None 
        
        conn = sqlite3.connect('almohit_modems.db')
        c = conn.cursor()
        c.execute("UPDATE settings SET value=? WHERE key='router_ip'", (new_ip,))
        c.execute("UPDATE settings SET value=? WHERE key='router_user'", (new_user,))
        c.execute("UPDATE settings SET value=? WHERE key='router_pass'", (new_pass,))
        conn.commit()
        conn.close()
        
        return jsonify({"message": f"تم إرسال طلب الاتصال بـ {current_host}..."})
    return jsonify({"message": "الرجاء إدخال جميع البيانات"})

@app.route("/api/markers", methods=['GET', 'POST'])
def manage_markers():
    conn = sqlite3.connect('almohit_modems.db')
    c = conn.cursor()
    if request.method == 'GET':
        c.execute("SELECT id, name, lat, lng, ip FROM modems")
        rows = c.fetchall()
        conn.close()
        return jsonify([{"id": r[0], "name": r[1], "lat": r[2], "lng": r[3], "ip": r[4]} for r in rows])
    elif request.method == 'POST':
        d = request.json
        c.execute("INSERT INTO modems (name, lat, lng, ip) VALUES (?, ?, ?, ?)", (d['name'], d['lat'], d['lng'], d.get('ip', '')))
        conn.commit()
        new_id = c.lastrowid
        conn.close()
        return jsonify({"id": new_id}), 201

@app.route("/api/markers/<int:id>", methods=['PUT', 'DELETE'])
def edit_marker(id):
    conn = sqlite3.connect('almohit_modems.db')
    c = conn.cursor()
    if request.method == 'PUT':
        d = request.json
        c.execute("UPDATE modems SET name=?, ip=?, lat=?, lng=? WHERE id=?", (d['name'], d.get('ip',''), d['lat'], d['lng'], id))
    elif request.method == 'DELETE':
        c.execute("DELETE FROM modems WHERE id=?", (id,))
    conn.commit()
    conn.close()
    return jsonify({"status": "success"})

if __name__ == "__main__":
    print("=== المحيط نت V15 (شامل مع إدخال كلمة المرور) ===")
    
    # قراءة البيانات المحفوظة
    conn = sqlite3.connect('almohit_modems.db')
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key='router_ip'"); row = c.fetchone()
    if row: current_host = row[0]
    c.execute("SELECT value FROM settings WHERE key='router_user'"); row_u = c.fetchone()
    if row_u: USER = row_u[0]
    c.execute("SELECT value FROM settings WHERE key='router_pass'"); row_p = c.fetchone()
    if row_p: PASS = row_p[0]
    conn.close()
    
    # إذا لم تكن هناك بيانات محفوظة، يطلبها في الشاشة السوداء
    if not USER or not PASS:
        USER = input("اسم مستخدم MikroTik (الافتراضي: admin): ").strip() or "admin"
        PASS = getpass.getpass("كلمة مرور MikroTik: ")
    
    threading.Thread(target=refresh_data, daemon=True).start()
    print("🌐 افتح المتصفح على: http://127.0.0.1:8080")
    app.run(host="0.0.0.0", port=8080, debug=False, threaded=True)
