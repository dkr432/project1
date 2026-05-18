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

# ===== 설정 =====
TH_IDLE    = 8000    # 이 이하면 가스 없음 (평상시)
TH_COOK    = 12000   # 이 이상이면 요리 시작 감지
TH_WARN    = 30000   # 주의
TH_DANGER  = 45000   # 위험

# ===== 상태 변수 =====
timer_duration  = 10 * 60  # 기본 타이머: 10분 (초 단위)
timer_start     = 0        # 타이머 시작 시각 (ticks_ms)
timer_running   = False    # 타이머 작동 중 여부
cooking         = False    # 요리 중 여부
last_gas        = 0        # 마지막 가스 수치
last_discord    = 0        # 마지막 디스코드 알림
last_sensor     = 0        # 마지막 센서 읽기
DISCORD_COOL    = 60000    # 위험 알림 쿨다운 60초
idle_count      = 0        # 가스 없음 카운트 (요리 종료 판단용)

# ===== LED =====
def clear_led():
    for i in range(NUM_LEDS):
        led[i] = (0, 0, 0)
    led.write()

def led_breathe_green():
    """평상시: 초록 숨쉬기"""
    t = time.ticks_ms()
    # 3초 주기로 밝기 0~30 왔다갔다
    brightness = int((1 + __import__('math').sin(t / 500)) * 15)
    for i in range(NUM_LEDS):
        led[i] = (0, brightness, 0)
    led.write()

def led_timer_progress(elapsed, total):
    """타이머 진행: 파란색 LED가 하나씩 꺼짐"""
    ratio = elapsed / total
    on_count = max(0, int(NUM_LEDS * (1 - ratio)))  # 남은 시간만큼 켜짐
    for i in range(NUM_LEDS):
        led[i] = (0, 0, 40) if i < on_count else (0, 0, 0)
    led.write()

def led_timer_warning():
    """타이머 임박: 노란색 깜빡"""
    b = (time.ticks_ms() // 400) % 2
    for i in range(NUM_LEDS):
        led[i] = (50, 25, 0) if b else (0, 0, 0)
    led.write()

def led_timer_done():
    """타이머 종료: 빨간색 깜빡"""
    b = (time.ticks_ms() // 300) % 2
    for i in range(NUM_LEDS):
        led[i] = (60, 0, 0) if b else (0, 0, 0)
    led.write()

def led_danger():
    """위험 감지: 빨간색 빠르게 번쩍"""
    b = (time.ticks_ms() // 80) % 2
    for i in range(NUM_LEDS):
        led[i] = (80, 0, 0) if b else (0, 0, 0)
    led.write()

def update_led():
    """상태에 따라 LED 자동 선택"""
    global timer_running, timer_start, timer_duration, cooking

    # 위험 감지 최우선
    if last_gas >= TH_DANGER:
        led_danger()
        return

    # 타이머 작동 중
    if timer_running:
        elapsed = time.ticks_diff(time.ticks_ms(), timer_start) / 1000
        remaining = timer_duration - elapsed

        if remaining <= 0:
            led_timer_done()
        elif remaining <= 60:  # 1분 이하면 임박
            led_timer_warning()
        else:
            led_timer_progress(elapsed, timer_duration)
        return

    # 평상시
    led_breathe_green()

# ===== 디스코드 =====
def discord_send(title, desc, color):
    try:
        import urequests
        d = json.dumps({
            "embeds": [{
                "title": title,
                "description": desc,
                "color": color
            }]
        })
        urequests.post(
            DISCORD_WEBHOOK_URL,
            data=d,
            headers={"Content-Type": "application/json"}
        )
        print(f"디스코드: {title}")
    except Exception as e:
        print(f"디스코드 실패: {e}")

def discord_timer_start():
    mins = timer_duration // 60
    secs = timer_duration % 60
    t = f"{mins}분" if secs == 0 else f"{mins}분 {secs}초"
    discord_send(
        "🍳 요리 시작!",
        f"가스레인지 감지!\n타이머: **{t}** 시작됩니다.",
        0x3498DB  # 파랑
    )

def discord_timer_done():
    discord_send(
        "⏰ 타이머 종료!",
        "요리 타이머가 끝났습니다!\n**가스레인지를 확인하세요!**",
        0xE67E22  # 주황
    )

def discord_danger(value):
    global last_discord
    now = time.ticks_ms()
    if time.ticks_diff(now, last_discord) < DISCORD_COOL:
        return
    discord_send(
        "🚨 위험! 가스 감지!",
        f"위험 수치 감지!\n수치: **{value} / 65535**\n즉시 환기하세요!",
        0xFF0000  # 빨강
    )
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
H1 = b"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>요리 안전 도우미</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:sans-serif;background:#0b0b12;color:#ddd;padding:15px}
h1{text-align:center;font-size:22px;padding:12px;color:#f90}
.r{display:flex;flex-wrap:wrap;gap:10px;justify-content:center;margin:10px 0}
.c{background:#13131e;border:1px solid #2a2a3a;border-radius:10px;
   padding:14px 18px;text-align:center;min-width:130px}
.lb{font-size:11px;color:#777;margin-bottom:4px}
.v{font-size:26px;font-weight:bold;margin:4px 0}
.badge{display:inline-block;padding:3px 14px;border-radius:12px;
       font-size:13px;font-weight:bold}
.b0{background:#0a3d0a;color:#4f4}
.b1{background:#3d3000;color:#fa0}
.b2{background:#3d0a0a;color:#f44;animation:p .5s infinite}
.b3{background:#0a1a3d;color:#48f}
.b4{background:#3d2000;color:#f90}
@keyframes p{50%{opacity:.5}}
"""

H2 = b""".timer-box{background:#13131e;border:1px solid #2a2a3a;border-radius:10px;
           padding:20px;max-width:600px;margin:12px auto;text-align:center}
.timer-box h2{color:#f90;margin-bottom:15px;font-size:16px}
.big-timer{font-size:52px;font-weight:bold;color:#fff;
           font-variant-numeric:tabular-nums;margin:10px 0}
.prog-outer{height:18px;background:#1a1a2e;border-radius:9px;
            overflow:hidden;margin:10px 0}
.prog-inner{height:100%;border-radius:9px;transition:width .5s}
.dots{display:flex;justify-content:center;gap:5px;margin:10px 0}
.dot{width:16px;height:16px;border-radius:50%;background:#1a1a2e;border:1px solid #333}
.d-green{background:#0c0;box-shadow:0 0 5px #0c0}
.d-blue{background:#48f;box-shadow:0 0 5px #48f}
.d-yellow{background:#fa0;box-shadow:0 0 5px #fa0}
.d-red{background:#f22;box-shadow:0 0 5px #f22}
"""

H3 = b""".set-box{background:#13131e;border:1px solid #2a2a3a;border-radius:10px;
         padding:20px;max-width:600px;margin:12px auto}
.set-box h2{color:#aaa;margin-bottom:15px;font-size:15px}
.row{display:flex;align-items:center;gap:10px;margin:8px 0;flex-wrap:wrap}
.row label{color:#888;font-size:13px;min-width:80px}
input[type=range]{flex:1;min-width:150px;accent-color:#f90}
input[type=number]{background:#1a1a2e;border:1px solid #333;color:#fff;
                   padding:4px 8px;border-radius:6px;width:70px;font-size:14px}
.btn{padding:10px 24px;border:none;border-radius:8px;font-size:14px;
     font-weight:bold;cursor:pointer;transition:.2s}
.btn-apply{background:#f90;color:#000}
.btn-apply:hover{background:#ffa}
.btn-stop{background:#f44;color:#fff;margin-left:8px}
.btn-stop:hover{background:#f88}
.gas-bar-o{height:16px;background:#1a1a2e;border-radius:8px;overflow:hidden;margin:8px 0}
.gas-bar-i{height:100%;border-radius:8px;transition:width .3s}
.ch{background:#13131e;border:1px solid #2a2a3a;border-radius:10px;
    padding:12px;max-width:600px;margin:12px auto}
</style></head><body>
<h1>🍳 요리 안전 도우미</h1>
"""

H4 = b"""<div class="r">
<div class="c"><div class="lb">가스 수치</div>
  <div class="v" id="gV">--</div></div>
<div class="c"><div class="lb">상태</div>
  <div style="margin:6px"><span class="badge b0" id="sB">대기중</span></div></div>
<div class="c"><div class="lb">타이머</div>
  <div class="v" style="color:#48f" id="tS">--:--</div></div>
</div>

<div class="timer-box">
<h2>⏱ 타이머</h2>
<div class="big-timer" id="bigT">--:--</div>
<div class="prog-outer"><div class="prog-inner" id="pI" style="width:0%;background:#48f"></div></div>
<div class="dots" id="dts">
<div class="dot"></div><div class="dot"></div><div class="dot"></div><div class="dot"></div><div class="dot"></div>
<div class="dot"></div><div class="dot"></div><div class="dot"></div><div class="dot"></div><div class="dot"></div>
</div>
</div>

<div class="set-box">
<h2>⚙️ 타이머 설정</h2>
<div class="row">
  <label>분</label>
  <input type="range" id="minR" min="1" max="60" value="10"
         oninput="document.getElementById('minN').value=this.value">
  <input type="number" id="minN" value="10" min="1" max="60"
         oninput="document.getElementById('minR').value=this.value">
</div>
<div class="row">
  <label>초</label>
  <input type="range" id="secR" min="0" max="59" value="0"
         oninput="document.getElementById('secN').value=this.value">
  <input type="number" id="secN" value="0" min="0" max="59"
         oninput="document.getElementById('secR').value=this.value">
</div>
<div class="row" style="margin-top:12px">
  <button class="btn btn-apply" onclick="applyTimer()">✅ 적용</button>
  <button class="btn btn-stop" onclick="stopTimer()">⏹ 타이머 중지</button>
</div>
<div id="applyMsg" style="color:#4f4;font-size:13px;margin-top:8px"></div>
</div>

<div class="set-box">
<h2>📊 가스 레벨</h2>
<div class="gas-bar-o"><div class="gas-bar-i" id="gBI" style="width:0%;background:#4f4"></div></div>
</div>

<div class="ch"><canvas id="gC"></canvas></div>
"""

H5 = b"""<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
let L=[],D=[],mx=0,ts=0,tc=0;
const ch=new Chart(document.getElementById('gC'),{
type:'line',data:{labels:L,datasets:[{data:D,
borderColor:'#f90',backgroundColor:'rgba(255,150,0,.1)',
borderWidth:2,fill:true,tension:.3,pointRadius:0}]},
options:{responsive:true,animation:{duration:100},
scales:{x:{ticks:{color:'#555',maxTicksLimit:6},grid:{color:'#1a1a2e'}},
y:{min:0,max:65535,ticks:{color:'#555'},grid:{color:'#1a1a2e'}}},
plugins:{legend:{display:false}}}});

function fmt(s){
  let m=Math.floor(Math.max(0,s)/60);
  let sc=Math.floor(Math.max(0,s)%60);
  return String(m).padStart(2,'0')+':'+String(sc).padStart(2,'0')}

function updateDots(state, ratio){
  let ds=document.querySelectorAll('.dot');
  let bl=Date.now()%600<300;
  ds.forEach((d,i)=>{
    d.className='dot';
    if(state==='idle') d.classList.add('d-green');
    else if(state==='running'){
      let n=Math.round((1-ratio)*10);
      if(i<n) d.classList.add('d-blue');
    } else if(state==='warning'){
      if(bl) d.classList.add('d-yellow');
    } else if(state==='done'||state==='danger'){
      if(bl) d.classList.add('d-red');
    }
  });
}

async function applyTimer(){
  let m=parseInt(document.getElementById('minN').value)||0;
  let s=parseInt(document.getElementById('secN').value)||0;
  let total=m*60+s;
  if(total<=0){alert('시간을 설정해주세요!');return;}
  try{
    let r=await fetch('/set_timer',{method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({duration:total})});
    let j=await r.json();
    document.getElementById('applyMsg').textContent=
      j.ok?'✅ 적용됨! 가스 감지 시 자동 시작':'❌ 실패';
    setTimeout(()=>document.getElementById('applyMsg').textContent='',3000);
  }catch(e){console.error(e);}
}

async function stopTimer(){
  try{
    await fetch('/stop_timer',{method:'POST'});
    document.getElementById('applyMsg').textContent='⏹ 타이머 중지됨';
    setTimeout(()=>document.getElementById('applyMsg').textContent='',3000);
  }catch(e){console.error(e);}
}

async function fetchData(){
  try{
    let r=await fetch('/data');
    let j=await r.json();
    let v=j.value;
    let now=new Date().toLocaleTimeString('ko-KR',{hour12:false});
    L.push('');D.push(v);
    if(L.length>100){L.shift();D.shift();}
    ch.update();

    // 수치 업데이트
    document.getElementById('gV').textContent=v;
    if(v>mx)mx=v;
    ts+=v;tc++;

    // 가스 바
    let pct=Math.min(100,v/65535*100);
    let gbi=document.getElementById('gBI');
    gbi.style.width=pct+'%';
    gbi.style.background=v<15000?'#4f4':v<30000?'#fa0':'#f22';

    // 상태 배지 + 타이머
    let sb=document.getElementById('sB');
    let state=j.state;
    let remaining=j.remaining;
    let duration=j.duration;

    if(state==='danger'){
      sb.textContent='🚨 위험!';sb.className='badge b2';
    } else if(state==='done'){
      sb.textContent='⏰ 종료!';sb.className='badge b4';
    } else if(state==='running'){
      sb.textContent='🍳 요리중';sb.className='badge b3';
    } else if(state==='warning'){
      sb.textContent='⚠️ 임박!';sb.className='badge b1';
    } else {
      sb.textContent='✅ 대기중';sb.className='badge b0';
    }

    // 타이머 표시
    let bigT=document.getElementById('bigT');
    let tS=document.getElementById('tS');
    let pI=document.getElementById('pI');

    if(state==='idle'||remaining===null){
      bigT.textContent='--:--';
      tS.textContent='--:--';
      pI.style.width='0%';
      updateDots('idle',0);
    } else {
      bigT.textContent=fmt(remaining);
      tS.textContent=fmt(remaining);
      let ratio=remaining/duration;
      let barColor=remaining<=60?'#f22':remaining<=120?'#fa0':'#48f';
      pI.style.width=(100*(1-ratio))+'%';
      pI.style.background=barColor;
      updateDots(state, remaining/duration);
    }

  }catch(e){console.error(e);}
}

setInterval(fetchData,500);
fetchData();
setInterval(()=>{
  let ds=document.querySelectorAll('.dot');
  // 깜빡 효과 유지
},150);
</script></body></html>"""

PARTS = [H1, H2, H3, H4, H5]

# ===== 서버 =====
def start_server(ip):
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 80))
    s.listen(2)
    s.settimeout(0.1)
    print(f"http://{ip} 접속하세요!")
    return s

def handle_client(cl):
    global timer_duration, timer_start, timer_running, cooking
    global idle_count

    try:
        cl.settimeout(3)
        raw = cl.recv(1024)
        req = raw.decode('utf-8', 'ignore')
        if not req:
            return

        line = req.split('\r\n')[0]
        parts = line.split(' ')
        method = parts[0] if len(parts) > 0 else 'GET'
        path   = parts[1] if len(parts) > 1 else '/'

        # ---- /data ----
        if path == '/data':
            elapsed = time.ticks_diff(time.ticks_ms(), timer_start) / 1000 if timer_running else 0
            remaining = max(0, timer_duration - elapsed) if timer_running else None

            # 상태 결정
            if last_gas >= TH_DANGER:
                state = 'danger'
            elif timer_running and remaining is not None and remaining <= 0:
                state = 'done'
            elif timer_running and remaining is not None and remaining <= 60:
                state = 'warning'
            elif timer_running:
                state = 'running'
            else:
                state = 'idle'

            body = json.dumps({
                "value": last_gas,
                "state": state,
                "remaining": int(remaining) if remaining is not None else None,
                "duration": timer_duration,
                "cooking": cooking
            })
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            cl.send(body.encode())

        # ---- /set_timer ----
        elif path == '/set_timer' and method == 'POST':
            # body 파싱
            body_str = req.split('\r\n\r\n')[-1]
            try:
                data = json.loads(body_str)
                timer_duration = int(data.get('duration', 600))
                timer_running = False  # 설정만, 시작은 가스 감지 시
                cooking = False
                idle_count = 0
                print(f"타이머 설정: {timer_duration}초")
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":true}')
            except:
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
                cl.send(b'{"ok":false}')

        # ---- /stop_timer ----
        elif path == '/stop_timer' and method == 'POST':
            timer_running = False
            cooking = False
            idle_count = 0
            print("타이머 중지")
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n")
            cl.send(b'{"ok":true}')

        # ---- /favicon.ico ----
        elif path == '/favicon.ico':
            cl.send(b"HTTP/1.1 204\r\n\r\n")

        # ---- HTML ----
        else:
            cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
            for part in PARTS:
                cl.send(part)
                time.sleep_ms(30)

    except Exception as e:
        print(f"요청 오류: {e}")
    finally:
        try:
            cl.close()
        except:
            pass

# ===== 메인 =====
def main():
    global last_gas, last_sensor, timer_running, timer_start
    global cooking, idle_count

    clear_led()
    gc.collect()

    ip = connect_wifi()
    if not ip:
        print("Wi-Fi 실패!")
        while True:
            time.sleep(1)

    server = start_server(ip)
    print("시스템 준비!")

    timer_done_notified = False  # 종료 알림 중복 방지

    while True:
        # 클라이언트 처리
        try:
            cl, addr = server.accept()
            handle_client(cl)
            gc.collect()
        except OSError:
            pass
        except Exception as e:
            print(f"서버 오류: {e}")

        # 센서 읽기 (0.5초마다)
        now = time.ticks_ms()
        if time.ticks_diff(now, last_sensor) >= 500:
            last_sensor = now
            last_gas = gas_sensor.read_u16()

            # 위험 감지
            if last_gas >= TH_DANGER:
                discord_danger(last_gas)

            # 요리 시작 감지 (타이머 미작동 중에만)
            if not timer_running and last_gas >= TH_COOK:
                idle_count = 0
                if not cooking:
                    cooking = True
                    timer_start = time.ticks_ms()
                    timer_running = True
                    timer_done_notified = False
                    print(f"요리 감지! 타이머 시작 ({timer_duration}초)")
                    discord_timer_start()

            # 요리 종료 감지 (가스 낮아지면)
            if cooking and last_gas < TH_IDLE:
                idle_count += 1
                if idle_count >= 6:  # 3초 연속 낮으면 종료
                    cooking = False
                    timer_running = False
                    idle_count = 0
                    timer_done_notified = False
                    print("가스 꺼짐 감지, 타이머 초기화")
            else:
                idle_count = 0

            # 타이머 종료 체크
            if timer_running:
                elapsed = time.ticks_diff(time.ticks_ms(), timer_start) / 1000
                if elapsed >= timer_duration and not timer_done_notified:
                    timer_done_notified = True
                    print("타이머 종료!")
                    discord_timer_done()

            # LED 업데이트
            update_led()

main()
