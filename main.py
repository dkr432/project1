import network
import socket
import time
import json
import gc
from machine import Pin, ADC
from neopixel import NeoPixel
from wifi_config import WIFI_NETWORKS, DISCORD_WEBHOOK_URL

# ===== 하드웨어 =====
gas_sensor = ADC(Pin(26))
TIMING = (280, 515, 515, 745)
NUM_LEDS = 10
led = NeoPixel(Pin(16), NUM_LEDS, timing=TIMING)

# ===== 임계값 =====
TH_IDLE   = 7000
TH_COOK   = 10000
TH_WARN   = 30000
TH_DANGER = 45000

# ===== 상태 변수 =====
timer_duration      = 600
timer_start         = 0
timer_running       = False
cooking             = False
last_gas            = 0
last_sensor         = 0
last_discord        = 0
last_led            = 0
idle_count          = 0
timer_done_notified = False
DISCORD_COOL        = 60000

# ===== LED =====
def clear_led():
    for i in range(NUM_LEDS):
        led[i] = (0, 0, 0)
    led.write()

def set_all(r, g, b):
    for i in range(NUM_LEDS):
        led[i] = (r, g, b)
    led.write()

def update_led():
    t = time.ticks_ms()

    # 1순위: 위험 → 빨강 빠른 번쩍
    if last_gas >= TH_DANGER:
        b = (t // 80) % 2
        if b:
            set_all(80, 0, 0)
        else:
            clear_led()
        return

    # 2순위: 가스 주의 → 노란색 천천히 깜빡
    if last_gas >= TH_WARN:
        b = (t // 500) % 2
        if b:
            set_all(60, 30, 0)
        else:
            clear_led()
        return

    # 3순위: 타이머 작동 중
    if timer_running:
        elapsed   = time.ticks_diff(t, timer_start) / 1000
        remaining = max(0, timer_duration - elapsed)

        # 타이머 종료 → 빨강 깜빡
        if remaining <= 0:
            b = (t // 300) % 2
            if b:
                set_all(60, 0, 0)
            else:
                clear_led()
            return

        # 타이머 1분 이하 → 노란색 빠르게 깜빡
        if remaining <= 60:
            b = (t // 200) % 2
            if b:
                set_all(50, 30, 0)
            else:
                clear_led()
            return

        # 타이머 진행 중 → 파란색 하나씩 꺼짐
        ratio  = elapsed / timer_duration
        on_cnt = max(0, int(NUM_LEDS * (1 - ratio)))
        for i in range(NUM_LEDS):
            led[i] = (0, 0, 50) if i < on_cnt else (0, 0, 0)
        led.write()
        return

    # 4순위: 평상시 → 초록 숨쉬기
    brightness = int((t % 3000) / 3000 * 25)
    if (t % 6000) >= 3000:
        brightness = 25 - brightness
    for i in range(NUM_LEDS):
        led[i] = (0, brightness, 0)
    led.write()

# ===== 상태 계산 =====
def get_state():
    elapsed   = time.ticks_diff(time.ticks_ms(), timer_start) / 1000 if timer_running else 0
    remaining = max(0, timer_duration - elapsed) if timer_running else None

    if last_gas >= TH_DANGER:
        return 'danger', remaining
    if last_gas >= TH_WARN:
        return 'gas_warn', remaining
    if timer_running and remaining is not None and remaining <= 0:
        return 'done', remaining
    if timer_running and remaining is not None and remaining <= 60:
        return 'timer_warn', remaining
    if timer_running:
        return 'running', remaining
    return 'idle', remaining

# ===== 디스코드 =====
def discord_send(title, desc, color):
    try:
        import urequests
        d = json.dumps({"embeds": [{"title": title, "description": desc, "color": color}]})
        urequests.post(DISCORD_WEBHOOK_URL, data=d, headers={"Content-Type": "application/json"})
        print(f"디스코드: {title}")
    except Exception as e:
        print(f"디스코드 실패: {e}")

def discord_timer_start():
    m = timer_duration // 60
    s = timer_duration % 60
    t = f"{m}분" if s == 0 else f"{m}분 {s}초"
    discord_send("🍳 요리 시작!", f"가스레인지 감지!\n타이머: **{t}** 시작!", 0x3498DB)

def discord_timer_done():
    discord_send("⏰ 타이머 종료!", "요리 타이머 끝!\n**가스레인지를 확인하세요!**", 0xE67E22)

def discord_danger(value):
    global last_discord
    now = time.ticks_ms()
    if time.ticks_diff(now, last_discord) < DISCORD_COOL:
        return
    discord_send("🚨 위험! 가스 감지!", f"위험 수치: **{value}/65535**\n즉시 환기하세요!", 0xFF0000)
    last_discord = now

# ===== Wi-Fi =====
def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    scan = [s[0].decode() for s in wlan.scan()]
    print(f"네트워크: {scan}")
    for ssid, pw in WIFI_NETWORKS.items():
        if ssid in scan:
            print(f"'{ssid}' 연결 중...")
            wlan.connect(ssid, pw)
            t = 15
            while not wlan.isconnected() and t > 0:
                time.sleep(1)
                t -= 1
            if wlan.isconnected():
                ip = wlan.ifconfig()[0]
                print(f"연결! IP: {ip}")
                return ip
            wlan.disconnect()
    return None

# ===== HTML =====
def get_html():
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>요리 안전 도우미</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:sans-serif;background:#0b0b12;color:#ddd;padding:15px}
h1{text-align:center;font-size:22px;padding:12px;color:#f90}
.r{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin:10px 0}
.c{background:#13131e;border:1px solid #2a2a3a;border-radius:10px;padding:14px 18px;text-align:center;min-width:130px}
.lb{font-size:11px;color:#777;margin-bottom:4px}
.v{font-size:26px;font-weight:bold;margin:4px 0}
.badge{display:inline-block;padding:3px 14px;border-radius:12px;font-size:13px;font-weight:bold}
.b0{background:#0a3d0a;color:#4f4}
.b1{background:#3d3000;color:#fa0}
.b2{background:#3d0a0a;color:#f44;animation:p .5s infinite}
.b3{background:#0a1a3d;color:#48f}
.b4{background:#3d2000;color:#f90}
.b5{background:#3d3000;color:#fa0;animation:p .5s infinite}
@keyframes p{50%{opacity:.5}}
.box{background:#13131e;border:1px solid #2a2a3a;border-radius:10px;padding:18px;max-width:620px;margin:12px auto}
.box h2{color:#aaa;margin-bottom:14px;font-size:15px}
.big-timer{font-size:52px;font-weight:bold;color:#fff;text-align:center;margin:10px 0}
.prog-o{height:18px;background:#1a1a2e;border-radius:9px;overflow:hidden;margin:10px 0}
.prog-i{height:100%;border-radius:9px;transition:width .5s}
.dots{display:flex;justify-content:center;gap:5px;margin:10px 0}
.dot{width:16px;height:16px;border-radius:50%;background:#1a1a2e;border:1px solid #333}
.d-g{background:#0c0;box-shadow:0 0 5px #0c0}
.d-b{background:#48f;box-shadow:0 0 5px #48f}
.d-y{background:#fa0;box-shadow:0 0 5px #fa0}
.d-r{background:#f22;box-shadow:0 0 5px #f22}
.row{display:flex;align-items:center;gap:10px;margin:8px 0;flex-wrap:wrap}
.row label{color:#888;font-size:13px;min-width:120px}
input[type=range]{flex:1;min-width:120px;accent-color:#f90}
input[type=number]{background:#1a1a2e;border:1px solid #333;color:#fff;padding:4px 8px;border-radius:6px;width:80px;font-size:14px}
.btn{padding:9px 20px;border:none;border-radius:8px;font-size:14px;font-weight:bold;cursor:pointer;margin:3px}
.btn-o{background:#f90;color:#000}
.btn-r{background:#f44;color:#fff}
.btn-b{background:#48f;color:#fff}
.msg{font-size:13px;margin-top:8px;min-height:18px}
.bar-o{height:14px;background:#1a1a2e;border-radius:7px;overflow:hidden;margin:6px 0}
.bar-i{height:100%;border-radius:7px;transition:width .3s}
.cur-val{font-size:12px;color:#f90;margin-left:6px}
</style>
</head>
<body>
<h1>요리 안전 도우미</h1>
<div class="r">
<div class="c"><div class="lb">가스 수치</div><div class="v" id="gV">--</div></div>
<div class="c"><div class="lb">상태</div><div style="margin:6px"><span class="badge b0" id="sB">대기중</span></div></div>
<div class="c"><div class="lb">남은 시간</div><div class="v" style="color:#48f" id="tS">--:--</div></div>
</div>
<div class="box">
<h2 style="text-align:center;color:#f90">타이머</h2>
<div class="big-timer" id="bigT">--:--</div>
<div class="prog-o"><div class="prog-i" id="pI" style="width:0%;background:#48f"></div></div>
<div class="dots">
<div class="dot"></div><div class="dot"></div><div class="dot"></div>
<div class="dot"></div><div class="dot"></div><div class="dot"></div>
<div class="dot"></div><div class="dot"></div><div class="dot"></div>
<div class="dot"></div>
</div>
</div>
<div class="box">
<h2>타이머 설정</h2>
<div class="row">
<label>분</label>
<input type="range" id="minR" min="1" max="60" value="10" oninput="minN.value=this.value">
<input type="number" id="minN" value="10" min="1" max="60" oninput="minR.value=this.value">
</div>
<div class="row">
<label>초</label>
<input type="range" id="secR" min="0" max="59" value="0" oninput="secN.value=this.value">
<input type="number" id="secN" value="0" min="0" max="59" oninput="secR.value=this.value">
</div>
<div>
<button class="btn btn-o" onclick="applyTimer()">타이머 적용</button>
<button class="btn btn-r" onclick="stopTimer()">중지</button>
</div>
<div class="msg" id="timerMsg"></div>
</div>
<div class="box">
<h2>감지 임계값 설정</h2>
<div class="row">
<label>가스없음<span class="cur-val" id="idleV">7000</span></label>
<input type="range" id="idleR" min="1000" max="20000" step="500" value="7000" oninput="idleV.textContent=this.value;idleN.value=this.value">
<input type="number" id="idleN" value="7000" min="1000" max="20000" oninput="idleR.value=this.value;idleV.textContent=this.value">
</div>
<div class="row">
<label>요리감지<span class="cur-val" id="cookV">10000</span></label>
<input type="range" id="cookR" min="1000" max="40000" step="500" value="10000" oninput="cookV.textContent=this.value;cookN.value=this.value">
<input type="number" id="cookN" value="10000" min="1000" max="40000" oninput="cookR.value=this.value;cookV.textContent=this.value">
</div>
<div class="row">
<label>주의<span class="cur-val" id="warnV">30000</span></label>
<input type="range" id="warnR" min="5000" max="60000" step="500" value="30000" oninput="warnV.textContent=this.value;warnN.value=this.value">
<input type="number" id="warnN" value="30000" min="5000" max="60000" oninput="warnR.value=this.value;warnV.textContent=this.value">
</div>
<div class="row">
<label>위험<span class="cur-val" id="dangerV">45000</span></label>
<input type="range" id="dangerR" min="10000" max="65000" step="500" value="45000" oninput="dangerV.textContent=this.value;dangerN.value=this.value">
<input type="number" id="dangerN" value="45000" min="10000" max="65000" oninput="dangerR.value=this.value;dangerV.textContent=this.value">
</div>
<div>
<button class="btn btn-b" onclick="applyThresh()">임계값 적용</button>
</div>
<div class="msg" id="threshMsg"></div>
</div>
<div class="box">
<h2>가스 레벨</h2>
<div class="bar-o"><div class="bar-i" id="gBI" style="width:0%;background:#4f4"></div></div>
</div>
<div class="box"><canvas id="gC"></canvas></div>
<script>
let L=[],D=[],curTH={idle:7000,cook:10000,warn:30000,danger:45000},threshLoaded=false;
const ch=new Chart(document.getElementById('gC'),{type:'line',data:{labels:L,datasets:[{data:D,borderColor:'#f90',backgroundColor:'rgba(255,150,0,.1)',borderWidth:2,fill:true,tension:.3,pointRadius:0}]},options:{responsive:true,animation:{duration:100},scales:{x:{ticks:{color:'#555',maxTicksLimit:6},grid:{color:'#1a1a2e'}},y:{min:0,max:65535,ticks:{color:'#555'},grid:{color:'#1a1a2e'}}},plugins:{legend:{display:false}}}});
function fmt(s){let m=Math.floor(Math.max(0,s)/60),sc=Math.floor(Math.max(0,s)%60);return String(m).padStart(2,'0')+':'+String(sc).padStart(2,'0')}
function updateDots(state,ratio){
  let ds=document.querySelectorAll('.dot'),bl=Date.now()%600<300;
  ds.forEach((d,i)=>{
    d.className='dot';d.style.opacity='1';
    if(state==='idle')d.classList.add('d-g');
    else if(state==='running'){if(i<Math.round((1-ratio)*10))d.classList.add('d-b');}
    else if(state==='gas_warn'){if(bl)d.classList.add('d-y');}
    else if(state==='timer_warn'){if(bl)d.classList.add('d-y');}
    else if(state==='done'||state==='danger'){if(bl)d.classList.add('d-r');}
  });
}
async function applyTimer(){
  let m=parseInt(minN.value)||0,s=parseInt(secN.value)||0,total=m*60+s;
  if(total<=0){alert('시간을 설정해주세요!');return;}
  let r=await fetch('/set_timer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({duration:total})});
  let j=await r.json();
  let msg=document.getElementById('timerMsg');
  msg.style.color=j.ok?'#4f4':'#f44';
  msg.textContent=j.ok?'적용! 가스 감지 시 자동 시작':'실패';
  setTimeout(()=>msg.textContent='',3000);
}
async function stopTimer(){
  await fetch('/stop_timer',{method:'POST'});
  let msg=document.getElementById('timerMsg');
  msg.style.color='#f44';msg.textContent='타이머 중지됨';
  setTimeout(()=>msg.textContent='',3000);
}
async function applyThresh(){
  let data={idle:parseInt(idleN.value),cook:parseInt(cookN.value),warn:parseInt(warnN.value),danger:parseInt(dangerN.value)};
  let r=await fetch('/set_thresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  let j=await r.json();
  let msg=document.getElementById('threshMsg');
  msg.style.color=j.ok?'#4f4':'#f44';
  msg.textContent=j.ok?'임계값 적용됨!':'실패';
  if(j.ok){curTH=data;threshLoaded=true;}
  setTimeout(()=>msg.textContent='',3000);
}
async function fetchData(){
  try{
    let r=await fetch('/data');
    let j=await r.json();
    let v=j.value;
    if(j.thresholds&&!threshLoaded){
      let th=j.thresholds;curTH=th;
      idleR.value=idleN.value=idleV.textContent=th.idle;
      cookR.value=cookN.value=cookV.textContent=th.cook;
      warnR.value=warnN.value=warnV.textContent=th.warn;
      dangerR.value=dangerN.value=dangerV.textContent=th.danger;
      threshLoaded=true;
    }
    L.push('');D.push(v);
    if(L.length>100){L.shift();D.shift();}
    ch.update();
    document.getElementById('gV').textContent=v;
    let pct=Math.min(100,v/65535*100);
    let gbi=document.getElementById('gBI');
    gbi.style.width=pct+'%';
    gbi.style.background=v<curTH.cook?'#4f4':v<curTH.warn?'#fa0':'#f22';
    let sb=document.getElementById('sB');
    let state=j.state,remaining=j.remaining,duration=j.duration;

    // ★ 상태별 배지 (gas_warn, timer_warn 구분)
    if(state==='danger'){sb.textContent='🚨 위험!';sb.className='badge b2';}
    else if(state==='gas_warn'){sb.textContent='⚠️ 가스주의!';sb.className='badge b5';}
    else if(state==='done'){sb.textContent='⏰ 종료!';sb.className='badge b4';}
    else if(state==='timer_warn'){sb.textContent='⏰ 타이머임박!';sb.className='badge b1';}
    else if(state==='running'){sb.textContent='🍳 요리중';sb.className='badge b3';}
    else{sb.textContent='✅ 대기중';sb.className='badge b0';}

    if(remaining===null||remaining===undefined){
      document.getElementById('bigT').textContent='--:--';
      document.getElementById('tS').textContent='--:--';
      document.getElementById('pI').style.width='0%';
      updateDots('idle',0);
    }else{
      document.getElementById('bigT').textContent=fmt(remaining);
      document.getElementById('tS').textContent=fmt(remaining);
      let ratio=remaining/duration;
      let pi=document.getElementById('pI');
      pi.style.width=(100*(1-ratio))+'%';
      pi.style.background=remaining<=60?'#f22':remaining<=120?'#fa0':'#48f';
      updateDots(state,ratio);
    }
  }catch(e){console.error('오류:',e);}
}
setInterval(fetchData,500);
fetchData();
setInterval(()=>{
  let bl=Date.now()%600<300;
  document.querySelectorAll('.dot').forEach(d=>{
    if(d.classList.contains('d-y')||d.classList.contains('d-r'))d.style.opacity=bl?'1':'0';
  });
},150);
</script>
</body>
</html>"""

# ===== 서버 =====
def send_html(cl):
    html = get_html()
    cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
    for i in range(0, len(html), 512):
        cl.send(html[i:i+512].encode('utf-8'))
        time.sleep_ms(20)

def handle_client(cl):
    global timer_duration, timer_start, timer_running
    global cooking, idle_count, timer_done_notified
    global TH_IDLE, TH_COOK, TH_WARN, TH_DANGER

    try:
        cl.settimeout(5)
        raw    = cl.recv(1024)
        req    = raw.decode('utf-8', 'ignore')
        if not req:
            return
        line   = req.split('\r\n')[0]
        parts  = line.split(' ')
        method = parts[0] if parts else 'GET'
        path   = parts[1] if len(parts) > 1 else '/'
        print(f"{method} {path}")

        if path == '/data':
            state, remaining = get_state()
            body = json.dumps({
                "value":      last_gas,
                "state":      state,
                "remaining":  int(remaining) if remaining is not None else None,
                "duration":   timer_duration,
                "thresholds": {"idle": TH_IDLE, "cook": TH_COOK, "warn": TH_WARN, "danger": TH_DANGER}
            })
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            cl.send(body.encode())

        elif path == '/set_timer' and method == 'POST':
            try:
                data                = json.loads(req.split('\r\n\r\n')[-1])
                timer_duration      = int(data.get('duration', 600))
                timer_running       = False
                cooking             = False
                idle_count          = 0
                timer_done_notified = False
                print(f"타이머 설정: {timer_duration}초")
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":true}')
            except:
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":false}')

        elif path == '/stop_timer' and method == 'POST':
            timer_running       = False
            cooking             = False
            idle_count          = 0
            timer_done_notified = False
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            cl.send(b'{"ok":true}')

        elif path == '/set_thresh' and method == 'POST':
            try:
                data      = json.loads(req.split('\r\n\r\n')[-1])
                TH_IDLE   = int(data.get('idle',   TH_IDLE))
                TH_COOK   = int(data.get('cook',   TH_COOK))
                TH_WARN   = int(data.get('warn',   TH_WARN))
                TH_DANGER = int(data.get('danger', TH_DANGER))
                print(f"임계값: idle={TH_IDLE} cook={TH_COOK} warn={TH_WARN} danger={TH_DANGER}")
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":true}')
            except:
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":false}')

        elif path == '/favicon.ico':
            cl.send(b"HTTP/1.1 204\r\n\r\n")

        else:
            send_html(cl)

    except Exception as e:
        print(f"요청 오류: {e}")
    finally:
        try:
            cl.close()
        except:
            pass

# ===== 메인 =====
def main():
    global last_gas, last_sensor, last_led
    global timer_running, timer_start
    global cooking, idle_count, timer_done_notified

    clear_led()
    gc.collect()

    ip = connect_wifi()
    if not ip:
        print("Wi-Fi 실패!")
        while True:
            time.sleep(1)

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 80))
    s.listen(2)
    s.settimeout(0.1)
    print(f"http://{ip} 접속하세요!")

    while True:
        try:
            cl, addr = s.accept()
            handle_client(cl)
            gc.collect()
        except OSError:
            pass
        except Exception as e:
            print(f"서버 오류: {e}")

        now = time.ticks_ms()

        if time.ticks_diff(now, last_led) >= 100:
            last_led = now
            update_led()

        if time.ticks_diff(now, last_sensor) >= 500:
            last_sensor = now
            last_gas    = gas_sensor.read_u16()
            print(f"가스: {last_gas}")

            if last_gas >= TH_DANGER:
                discord_danger(last_gas)

            if not timer_running and last_gas >= TH_COOK:
                idle_count = 0
                if not cooking:
                    cooking             = True
                    timer_start         = time.ticks_ms()
                    timer_running       = True
                    timer_done_notified = False
                    print(f"요리 감지! {timer_duration}초 타이머 시작")
                    discord_timer_start()

            if cooking and last_gas < TH_IDLE:
                idle_count += 1
                if idle_count >= 6:
                    cooking             = False
                    timer_running       = False
                    idle_count          = 0
                    timer_done_notified = False
                    print("가스 꺼짐, 타이머 초기화")
            else:
                if last_gas >= TH_IDLE:
                    idle_count = 0

            if timer_running:
                elapsed = time.ticks_diff(time.ticks_ms(), timer_start) / 1000
                if elapsed >= timer_duration and not timer_done_notified:
                    timer_done_notified = True
                    print("타이머 종료!")
                    discord_timer_done()

main()
