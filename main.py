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

    if last_gas >= TH_DANGER:
        b = (t // 80) % 2
        set_all(80, 0, 0) if b else clear_led()
        return

    if last_gas >= TH_WARN:
        b = (t // 500) % 2
        set_all(60, 30, 0) if b else clear_led()
        return

    if timer_running:
        elapsed   = time.ticks_diff(t, timer_start) / 1000
        remaining = max(0, timer_duration - elapsed)

        if remaining <= 0:
            b = (t // 300) % 2
            set_all(60, 0, 0) if b else clear_led()
            return

        if remaining <= 60:
            b = (t // 200) % 2
            set_all(50, 30, 0) if b else clear_led()
            return

        ratio  = elapsed / timer_duration
        on_cnt = max(0, int(NUM_LEDS * (1 - ratio)))
        for i in range(NUM_LEDS):
            led[i] = (0, 0, 50) if i < on_cnt else (0, 0, 0)
        led.write()
        return

    brightness = int((t % 3000) / 3000 * 25)
    if (t % 6000) >= 3000:
        brightness = 25 - brightness
    for i in range(NUM_LEDS):
        led[i] = (0, brightness, 0)
    led.write()

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

# ===== HTML (완전 리뉴얼!) =====
def get_html():
    return """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🔥 SmartChef AI 🔥</title>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Noto+Sans+KR:wght@400;700;900&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{
  font-family:'Noto Sans KR',sans-serif;
  background:#000;
  color:#fff;
  min-height:100vh;
  overflow-x:hidden;
  position:relative;
}

/* 배경 애니메이션 */
body::before{
  content:'';
  position:fixed;
  top:0;left:0;width:100%;height:100%;
  background:
    radial-gradient(circle at 20% 30%,rgba(255,100,0,.15),transparent 50%),
    radial-gradient(circle at 80% 70%,rgba(255,0,100,.15),transparent 50%),
    radial-gradient(circle at 50% 50%,rgba(100,0,255,.1),transparent 50%);
  animation:bgMove 15s ease infinite;
  z-index:-2;
}
@keyframes bgMove{
  0%,100%{transform:scale(1) rotate(0deg)}
  50%{transform:scale(1.2) rotate(180deg)}
}

/* 격자 배경 */
body::after{
  content:'';
  position:fixed;
  top:0;left:0;width:100%;height:100%;
  background:
    linear-gradient(rgba(255,150,0,.03) 1px,transparent 1px),
    linear-gradient(90deg,rgba(255,150,0,.03) 1px,transparent 1px);
  background-size:40px 40px;
  z-index:-1;
  animation:gridMove 20s linear infinite;
}
@keyframes gridMove{
  0%{background-position:0 0}
  100%{background-position:40px 40px}
}

.container{padding:20px;max-width:1200px;margin:0 auto}

/* 헤더 */
.header{
  text-align:center;
  padding:30px 0 20px;
  position:relative;
}
.header h1{
  font-family:'Orbitron',sans-serif;
  font-size:clamp(28px,5vw,48px);
  font-weight:900;
  background:linear-gradient(90deg,#ff6b00,#ff0080,#ff6b00);
  background-size:200% 100%;
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
  background-clip:text;
  animation:shine 3s linear infinite;
  letter-spacing:3px;
  text-shadow:0 0 30px rgba(255,107,0,.5);
}
@keyframes shine{
  0%{background-position:0% 50%}
  100%{background-position:200% 50%}
}
.header .sub{
  font-size:13px;
  color:#888;
  margin-top:8px;
  letter-spacing:5px;
}

/* 상태 카드 */
.status-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(200px,1fr));
  gap:15px;
  margin:25px 0;
}
.scard{
  background:linear-gradient(135deg,rgba(20,20,40,.9),rgba(40,20,40,.9));
  border:1px solid rgba(255,107,0,.3);
  border-radius:16px;
  padding:20px;
  text-align:center;
  position:relative;
  overflow:hidden;
  backdrop-filter:blur(10px);
  transition:all .3s;
}
.scard:hover{
  transform:translateY(-5px);
  border-color:rgba(255,107,0,.8);
  box-shadow:0 10px 30px rgba(255,107,0,.3);
}
.scard::before{
  content:'';
  position:absolute;
  top:-50%;left:-50%;width:200%;height:200%;
  background:linear-gradient(45deg,transparent,rgba(255,107,0,.1),transparent);
  animation:cardShine 4s linear infinite;
}
@keyframes cardShine{
  0%{transform:translateX(-100%) translateY(-100%)}
  100%{transform:translateX(100%) translateY(100%)}
}
.scard-lb{
  font-size:11px;
  color:#888;
  letter-spacing:2px;
  margin-bottom:8px;
  text-transform:uppercase;
}
.scard-v{
  font-family:'Orbitron',sans-serif;
  font-size:36px;
  font-weight:900;
  background:linear-gradient(135deg,#ff6b00,#ff0080);
  -webkit-background-clip:text;
  -webkit-text-fill-color:transparent;
  background-clip:text;
}

/* 배지 */
.badge{
  display:inline-block;
  padding:8px 20px;
  border-radius:20px;
  font-size:13px;
  font-weight:900;
  letter-spacing:2px;
  text-transform:uppercase;
}
.b0{background:linear-gradient(135deg,#0a7d3a,#0e9d4a);box-shadow:0 0 20px rgba(15,200,80,.5)}
.b1{background:linear-gradient(135deg,#cc6600,#ff8800);box-shadow:0 0 20px rgba(255,150,0,.6);animation:p .5s infinite}
.b2{background:linear-gradient(135deg,#cc0000,#ff0044);box-shadow:0 0 30px rgba(255,0,68,.8);animation:p .3s infinite}
.b3{background:linear-gradient(135deg,#0044cc,#0080ff);box-shadow:0 0 20px rgba(0,128,255,.6)}
.b4{background:linear-gradient(135deg,#ff4400,#ff8800);box-shadow:0 0 25px rgba(255,100,0,.7);animation:p .4s infinite}
.b5{background:linear-gradient(135deg,#ffaa00,#ffdd00);color:#000;box-shadow:0 0 25px rgba(255,200,0,.7);animation:p .5s infinite}
@keyframes p{50%{opacity:.4;transform:scale(.95)}}

/* 박스 */
.box{
  background:linear-gradient(135deg,rgba(15,15,30,.95),rgba(30,15,30,.95));
  border:1px solid rgba(255,107,0,.2);
  border-radius:20px;
  padding:25px;
  margin:20px 0;
  position:relative;
  backdrop-filter:blur(15px);
  box-shadow:0 8px 32px rgba(0,0,0,.5);
}
.box h2{
  font-family:'Orbitron',sans-serif;
  color:#ff6b00;
  font-size:16px;
  letter-spacing:3px;
  margin-bottom:20px;
  text-transform:uppercase;
  display:flex;
  align-items:center;
  gap:10px;
}
.box h2::before{
  content:'';
  width:4px;height:24px;
  background:linear-gradient(180deg,#ff6b00,#ff0080);
  border-radius:2px;
  box-shadow:0 0 10px rgba(255,107,0,.8);
}

/* 메인 타이머 */
.timer-main{
  text-align:center;
  padding:30px 0;
  position:relative;
}
.timer-ring{
  width:280px;height:280px;
  margin:0 auto 20px;
  position:relative;
}
.timer-svg{
  width:100%;height:100%;
  transform:rotate(-90deg);
  filter:drop-shadow(0 0 20px rgba(255,107,0,.5));
}
.timer-bg{fill:none;stroke:rgba(255,255,255,.05);stroke-width:8}
.timer-fill{
  fill:none;
  stroke:url(#grad1);
  stroke-width:8;
  stroke-linecap:round;
  transition:stroke-dashoffset .5s ease;
}
.timer-center{
  position:absolute;
  top:50%;left:50%;
  transform:translate(-50%,-50%);
  text-align:center;
}
.timer-num{
  font-family:'Orbitron',sans-serif;
  font-size:56px;
  font-weight:900;
  color:#fff;
  text-shadow:0 0 30px rgba(255,107,0,.8);
  line-height:1;
}
.timer-lb{
  font-size:11px;
  color:#888;
  letter-spacing:3px;
  margin-top:8px;
}

/* LED 시뮬레이션 */
.led-strip{
  display:flex;
  justify-content:center;
  gap:8px;
  margin:20px 0;
  padding:15px;
  background:rgba(0,0,0,.5);
  border-radius:15px;
  border:1px solid rgba(255,107,0,.2);
}
.led-dot{
  width:24px;height:24px;
  border-radius:50%;
  background:#1a1a2e;
  border:2px solid #333;
  transition:all .2s;
  position:relative;
}
.led-dot::after{
  content:'';
  position:absolute;
  top:-3px;left:-3px;right:-3px;bottom:-3px;
  border-radius:50%;
  opacity:0;
  transition:opacity .3s;
}
.led-on-g{background:radial-gradient(#0f0,#080);box-shadow:0 0 15px #0f0,0 0 30px rgba(0,255,0,.5)}
.led-on-b{background:radial-gradient(#48f,#06c);box-shadow:0 0 15px #48f,0 0 30px rgba(50,150,255,.5)}
.led-on-y{background:radial-gradient(#fa0,#c80);box-shadow:0 0 15px #fa0,0 0 30px rgba(255,170,0,.5)}
.led-on-r{background:radial-gradient(#f22,#a00);box-shadow:0 0 20px #f22,0 0 40px rgba(255,30,30,.7)}

/* 컨트롤 */
.row{
  display:flex;
  align-items:center;
  gap:15px;
  margin:15px 0;
  flex-wrap:wrap;
}
.row label{
  color:#aaa;
  font-size:13px;
  min-width:120px;
  font-weight:700;
  display:flex;
  align-items:center;
  gap:8px;
}
.cur-val{
  font-family:'Orbitron',sans-serif;
  color:#ff6b00;
  font-weight:900;
  font-size:14px;
}

/* 슬라이더 */
input[type=range]{
  flex:1;
  min-width:150px;
  -webkit-appearance:none;
  appearance:none;
  height:8px;
  background:linear-gradient(90deg,#1a1a2e,#2a1a3e);
  border-radius:4px;
  outline:none;
  cursor:pointer;
}
input[type=range]::-webkit-slider-thumb{
  -webkit-appearance:none;
  width:22px;height:22px;
  background:linear-gradient(135deg,#ff6b00,#ff0080);
  border-radius:50%;
  cursor:pointer;
  box-shadow:0 0 15px rgba(255,107,0,.8);
  transition:transform .2s;
}
input[type=range]::-webkit-slider-thumb:hover{transform:scale(1.3)}
input[type=range]::-moz-range-thumb{
  width:22px;height:22px;
  background:linear-gradient(135deg,#ff6b00,#ff0080);
  border-radius:50%;
  cursor:pointer;
  border:none;
  box-shadow:0 0 15px rgba(255,107,0,.8);
}

input[type=number]{
  background:rgba(0,0,0,.5);
  border:1px solid rgba(255,107,0,.3);
  color:#ff6b00;
  padding:8px 12px;
  border-radius:8px;
  width:90px;
  font-size:14px;
  font-family:'Orbitron',sans-serif;
  font-weight:700;
  text-align:center;
}
input[type=number]:focus{
  outline:none;
  border-color:#ff6b00;
  box-shadow:0 0 15px rgba(255,107,0,.5);
}

/* 버튼 */
.btn{
  padding:12px 28px;
  border:none;
  border-radius:10px;
  font-size:14px;
  font-weight:900;
  cursor:pointer;
  margin:5px;
  letter-spacing:2px;
  text-transform:uppercase;
  transition:all .3s;
  position:relative;
  overflow:hidden;
}
.btn::before{
  content:'';
  position:absolute;
  top:0;left:-100%;width:100%;height:100%;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);
  transition:left .5s;
}
.btn:hover::before{left:100%}
.btn:hover{transform:translateY(-2px)}
.btn:active{transform:translateY(0)}
.btn-o{
  background:linear-gradient(135deg,#ff6b00,#ff0080);
  color:#fff;
  box-shadow:0 5px 20px rgba(255,107,0,.4);
}
.btn-o:hover{box-shadow:0 8px 30px rgba(255,107,0,.6)}
.btn-r{
  background:linear-gradient(135deg,#cc0000,#ff0044);
  color:#fff;
  box-shadow:0 5px 20px rgba(255,0,68,.4);
}
.btn-r:hover{box-shadow:0 8px 30px rgba(255,0,68,.6)}
.btn-b{
  background:linear-gradient(135deg,#0044cc,#0080ff);
  color:#fff;
  box-shadow:0 5px 20px rgba(0,128,255,.4);
}
.btn-b:hover{box-shadow:0 8px 30px rgba(0,128,255,.6)}

.msg{
  font-size:14px;
  margin-top:12px;
  min-height:20px;
  font-weight:700;
  letter-spacing:1px;
}

/* 가스 미터 */
.gas-meter{
  height:30px;
  background:linear-gradient(90deg,#0a0a1e,#1a0a2e);
  border-radius:15px;
  overflow:hidden;
  margin:15px 0;
  position:relative;
  border:1px solid rgba(255,107,0,.2);
  box-shadow:inset 0 2px 10px rgba(0,0,0,.5);
}
.gas-fill{
  height:100%;
  border-radius:15px;
  transition:width .5s,background .3s;
  position:relative;
  overflow:hidden;
}
.gas-fill::after{
  content:'';
  position:absolute;
  top:0;left:0;right:0;bottom:0;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.3),transparent);
  animation:gasShine 2s linear infinite;
}
@keyframes gasShine{
  0%{transform:translateX(-100%)}
  100%{transform:translateX(100%)}
}

/* 차트 */
.chart-box{
  background:linear-gradient(135deg,rgba(10,10,30,.95),rgba(20,10,30,.95));
  border-radius:20px;
  padding:25px;
  margin:20px 0;
  border:1px solid rgba(255,107,0,.2);
  box-shadow:0 8px 32px rgba(0,0,0,.5);
}

/* 알림 효과 */
@keyframes pulseAlert{
  0%,100%{transform:scale(1)}
  50%{transform:scale(1.02)}
}
.alert-pulse{animation:pulseAlert 1s infinite}

/* 푸터 */
.footer{
  text-align:center;
  padding:30px 0;
  color:#555;
  font-size:11px;
  letter-spacing:3px;
}

/* 반응형 */
@media(max-width:600px){
  .timer-ring{width:220px;height:220px}
  .timer-num{font-size:42px}
  .header h1{font-size:28px;letter-spacing:1px}
}
</style>
</head>
<body>
<div class="container">

<div class="header">
  <h1>🔥 SMART CHEF AI 🔥</h1>
  <div class="sub">REAL-TIME COOKING SAFETY SYSTEM</div>
</div>

<div class="status-grid">
  <div class="scard">
    <div class="scard-lb">GAS LEVEL</div>
    <div class="scard-v" id="gV">--</div>
  </div>
  <div class="scard">
    <div class="scard-lb">STATUS</div>
    <div style="margin-top:10px"><span class="badge b0" id="sB">대기중</span></div>
  </div>
  <div class="scard">
    <div class="scard-lb">REMAINING</div>
    <div class="scard-v" id="tS" style="background:linear-gradient(135deg,#0080ff,#0044cc);-webkit-background-clip:text;-webkit-text-fill-color:transparent">--:--</div>
  </div>
</div>

<div class="box">
  <h2>⏱ COOKING TIMER</h2>
  <div class="timer-main">
    <div class="timer-ring">
      <svg class="timer-svg" viewBox="0 0 100 100">
        <defs>
          <linearGradient id="grad1" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" style="stop-color:#ff6b00"/>
            <stop offset="50%" style="stop-color:#ff0080"/>
            <stop offset="100%" style="stop-color:#8000ff"/>
          </linearGradient>
        </defs>
        <circle class="timer-bg" cx="50" cy="50" r="45"/>
        <circle class="timer-fill" id="timerFill" cx="50" cy="50" r="45"
                stroke-dasharray="282.7" stroke-dashoffset="282.7"/>
      </svg>
      <div class="timer-center">
        <div class="timer-num" id="bigT">--:--</div>
        <div class="timer-lb">REMAINING</div>
      </div>
    </div>

    <div class="led-strip">
      <div class="led-dot"></div><div class="led-dot"></div>
      <div class="led-dot"></div><div class="led-dot"></div>
      <div class="led-dot"></div><div class="led-dot"></div>
      <div class="led-dot"></div><div class="led-dot"></div>
      <div class="led-dot"></div><div class="led-dot"></div>
    </div>
  </div>
</div>

<div class="box">
  <h2>⚙ TIMER SETTINGS</h2>
  <div class="row">
    <label>⏰ MINUTES <span class="cur-val" id="minV">10</span></label>
    <input type="range" id="minR" min="0" max="60" value="10" oninput="minN.value=this.value;minV.textContent=this.value">
    <input type="number" id="minN" value="10" min="0" max="60" oninput="minR.value=this.value;minV.textContent=this.value">
  </div>
  <div class="row">
    <label>⏱ SECONDS <span class="cur-val" id="secV">0</span></label>
    <input type="range" id="secR" min="0" max="59" value="0" oninput="secN.value=this.value;secV.textContent=this.value">
    <input type="number" id="secN" value="0" min="0" max="59" oninput="secR.value=this.value;secV.textContent=this.value">
  </div>
  <div style="margin-top:15px">
    <button class="btn btn-o" onclick="applyTimer()">✅ 타이머 적용</button>
    <button class="btn btn-r" onclick="stopTimer()">⏹ 중지</button>
  </div>
  <div class="msg" id="timerMsg"></div>
</div>

<div class="box">
  <h2>🎚 SENSOR THRESHOLDS</h2>
  <div class="row">
    <label>💨 가스없음 <span class="cur-val" id="idleV">7000</span></label>
    <input type="range" id="idleR" min="1000" max="20000" step="500" value="7000" oninput="idleV.textContent=this.value;idleN.value=this.value">
    <input type="number" id="idleN" value="7000" min="1000" max="20000" oninput="idleR.value=this.value;idleV.textContent=this.value">
  </div>
  <div class="row">
    <label>🍳 요리감지 <span class="cur-val" id="cookV">10000</span></label>
    <input type="range" id="cookR" min="1000" max="40000" step="500" value="10000" oninput="cookV.textContent=this.value;cookN.value=this.value">
    <input type="number" id="cookN" value="10000" min="1000" max="40000" oninput="cookR.value=this.value;cookV.textContent=this.value">
  </div>
  <div class="row">
    <label>⚠ 주의 <span class="cur-val" id="warnV">30000</span></label>
    <input type="range" id="warnR" min="5000" max="60000" step="500" value="30000" oninput="warnV.textContent=this.value;warnN.value=this.value">
    <input type="number" id="warnN" value="30000" min="5000" max="60000" oninput="warnR.value=this.value;warnV.textContent=this.value">
  </div>
  <div class="row">
    <label>🚨 위험 <span class="cur-val" id="dangerV">45000</span></label>
    <input type="range" id="dangerR" min="10000" max="65000" step="500" value="45000" oninput="dangerV.textContent=this.value;dangerN.value=this.value">
    <input type="number" id="dangerN" value="45000" min="10000" max="65000" oninput="dangerR.value=this.value;dangerV.textContent=this.value">
  </div>
  <div style="margin-top:15px">
    <button class="btn btn-b" onclick="applyThresh()">✅ 임계값 적용</button>
  </div>
  <div class="msg" id="threshMsg"></div>
</div>

<div class="box">
  <h2>📊 LIVE GAS METER</h2>
  <div class="gas-meter"><div class="gas-fill" id="gBI" style="width:0%;background:linear-gradient(90deg,#0f0,#0a0)"></div></div>
  <div style="display:flex;justify-content:space-between;font-size:10px;color:#666;margin-top:8px;letter-spacing:1px">
    <span>0</span>
    <span style="color:#0f0">SAFE</span>
    <span style="color:#fa0">WARN</span>
    <span style="color:#f22">DANGER</span>
    <span>65535</span>
  </div>
</div>

<div class="chart-box">
  <h2 style="font-family:'Orbitron';color:#ff6b00;font-size:16px;letter-spacing:3px;margin-bottom:15px">📈 GAS LEVEL HISTORY</h2>
  <canvas id="gC"></canvas>
</div>

<div class="footer">SMART CHEF AI © 2024 · POWERED BY RASPBERRY PI PICO 2W</div>

</div>

<script>
let L=[],D=[],curTH={idle:7000,cook:10000,warn:30000,danger:45000},threshLoaded=false;

const ch=new Chart(document.getElementById('gC'),{
  type:'line',
  data:{labels:L,datasets:[{
    data:D,
    borderColor:'#ff6b00',
    backgroundColor:'rgba(255,107,0,.15)',
    borderWidth:3,
    fill:true,
    tension:.4,
    pointRadius:0,
    pointHoverRadius:6,
    pointHoverBackgroundColor:'#ff0080',
    pointHoverBorderColor:'#fff'
  }]},
  options:{
    responsive:true,
    animation:{duration:300},
    scales:{
      x:{ticks:{color:'#666',maxTicksLimit:6,font:{family:'Orbitron'}},grid:{color:'rgba(255,107,0,.05)'}},
      y:{min:0,max:65535,ticks:{color:'#666',font:{family:'Orbitron'}},grid:{color:'rgba(255,107,0,.05)'}}
    },
    plugins:{legend:{display:false}}
  }
});

function fmt(s){
  let m=Math.floor(Math.max(0,s)/60);
  let sc=Math.floor(Math.max(0,s)%60);
  return String(m).padStart(2,'0')+':'+String(sc).padStart(2,'0');
}

function updateLEDs(state,ratio){
  let ds=document.querySelectorAll('.led-dot');
  let bl=Date.now()%600<300;
  ds.forEach((d,i)=>{
    d.className='led-dot';
    if(state==='idle')d.classList.add('led-on-g');
    else if(state==='running'){
      if(i<Math.round((1-ratio)*10))d.classList.add('led-on-b');
    }
    else if(state==='gas_warn'){if(bl)d.classList.add('led-on-y');}
    else if(state==='timer_warn'){if(bl)d.classList.add('led-on-y');}
    else if(state==='done'||state==='danger'){if(bl)d.classList.add('led-on-r');}
  });
}

async function applyTimer(){
  let m=parseInt(minN.value)||0,s=parseInt(secN.value)||0,total=m*60+s;
  if(total<=0){alert('⚠️ 시간을 설정해주세요!');return;}
  let r=await fetch('/set_timer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({duration:total})});
  let j=await r.json();
  let msg=document.getElementById('timerMsg');
  msg.style.color=j.ok?'#0f0':'#f44';
  msg.textContent=j.ok?'✅ 타이머 적용! 가스 감지 시 자동 시작':'❌ 실패';
  setTimeout(()=>msg.textContent='',3000);
}

async function stopTimer(){
  await fetch('/stop_timer',{method:'POST'});
  let msg=document.getElementById('timerMsg');
  msg.style.color='#f44';
  msg.textContent='⏹ 타이머 중지됨';
  setTimeout(()=>msg.textContent='',3000);
}

async function applyThresh(){
  let data={idle:parseInt(idleN.value),cook:parseInt(cookN.value),warn:parseInt(warnN.value),danger:parseInt(dangerN.value)};
  let r=await fetch('/set_thresh',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  let j=await r.json();
  let msg=document.getElementById('threshMsg');
  msg.style.color=j.ok?'#0f0':'#f44';
  msg.textContent=j.ok?'✅ 임계값 적용됨!':'❌ 실패';
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
    ch.update('none');

    document.getElementById('gV').textContent=v;

    let pct=Math.min(100,v/65535*100);
    let gbi=document.getElementById('gBI');
    gbi.style.width=pct+'%';
    if(v<curTH.cook)gbi.style.background='linear-gradient(90deg,#0f0,#0a0)';
    else if(v<curTH.warn)gbi.style.background='linear-gradient(90deg,#fa0,#c80)';
    else gbi.style.background='linear-gradient(90deg,#f44,#a00)';

    let sb=document.getElementById('sB');
    let state=j.state,remaining=j.remaining,duration=j.duration;
    let body=document.body;
    body.classList.remove('alert-pulse');

    if(state==='danger'){
      sb.textContent='🚨 위험!';sb.className='badge b2';
      body.classList.add('alert-pulse');
    }
    else if(state==='gas_warn'){sb.textContent='⚠ 가스주의';sb.className='badge b5';}
    else if(state==='done'){sb.textContent='⏰ 종료!';sb.className='badge b4';}
    else if(state==='timer_warn'){sb.textContent='⏰ 임박!';sb.className='badge b1';}
    else if(state==='running'){sb.textContent='🍳 요리중';sb.className='badge b3';}
    else{sb.textContent='✅ 대기중';sb.className='badge b0';}

    let bigT=document.getElementById('bigT');
    let tS=document.getElementById('tS');
    let fill=document.getElementById('timerFill');

    if(remaining===null||remaining===undefined){
      bigT.textContent='--:--';tS.textContent='--:--';
      fill.style.strokeDashoffset='282.7';
      updateLEDs('idle',0);
    }else{
      bigT.textContent=fmt(remaining);
      tS.textContent=fmt(remaining);
      let ratio=remaining/duration;
      fill.style.strokeDashoffset=(282.7*(1-ratio))+'';
      updateLEDs(state,ratio);
    }
  }catch(e){console.error('오류:',e);}
}

setInterval(fetchData,500);
fetchData();

setInterval(()=>{
  let bl=Date.now()%600<300;
  document.querySelectorAll('.led-dot').forEach(d=>{
    if(d.classList.contains('led-on-y')||d.classList.contains('led-on-r')){
      d.style.opacity=bl?'1':'.2';
    }else{
      d.style.opacity='1';
    }
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
                print(f"임계값: {TH_IDLE} {TH_COOK} {TH_WARN} {TH_DANGER}")
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
