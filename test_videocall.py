"""Full Video Call Test: Core + Engine + OpenClaw + TTS + Browser UI.

Flow:
  1. Browser: user types message
  2. Server: sends to OpenClaw agent via gateway
  3. Server: agent responds with text
  4. Server: Piper TTS → audio
  5. Server: Core/Engine → animation weights
  6. Server: sends audio + weights to browser
  7. Browser: plays audio + animates avatar in sync

Open: https://openclaw-vaibhav.tail72d21d.ts.net:8766
"""

import asyncio
import base64
import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/vaibhav/clawvatar-core")
sys.path.insert(0, "/home/vaibhav/clawvatar-engine")

import numpy as np
import uvicorn
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from clawvatar_core.config import CoreConfig, EngineConfig
from clawvatar_core.engine.embedded import EmbeddedEngineClient
from clawvatar_core.adapters.openclaw import OpenClawAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("videocall")

# --- TTS ---
PIPER_MODEL = "/tmp/piper-models/en_US-lessac-medium.onnx"
_piper = None

def get_piper():
    global _piper
    if _piper is None:
        from piper import PiperVoice
        _piper = PiperVoice.load(PIPER_MODEL)
    return _piper

def synthesize(text):
    v = get_piper()
    sr = v.config.sample_rate
    chunks = []
    for c in v.synthesize(text):
        chunks.append(c.audio_int16_bytes)
    return b"".join(chunks), sr

# --- Engine ---
engine = None

async def get_engine():
    global engine
    if engine is None:
        engine = EmbeddedEngineClient(EngineConfig())
        await engine.connect()
    return engine

# --- OpenClaw ---
oc_adapter = None

async def get_openclaw():
    global oc_adapter
    if oc_adapter is None:
        url, token = OpenClawAdapter.read_config()
        oc_adapter = OpenClawAdapter(gateway_url=url, token=token)
        await oc_adapter.connect()
        logger.info("OpenClaw connected")
    return oc_adapter

# --- App ---
app = FastAPI(title="Clawvatar Video Call")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
AVATAR_DIR = Path.home() / ".clawvatar" / "avatars"

@app.on_event("startup")
async def startup():
    await get_engine()
    # Load avatar if available
    for name in ["Juanita.vrm", "juanita.vrm"]:
        p = AVATAR_DIR / name
        if p.exists():
            await engine.load_avatar(str(p))
            break
    # Connect to OpenClaw
    try:
        await get_openclaw()
    except Exception as e:
        logger.warning(f"OpenClaw not available: {e}")

@app.post("/upload")
async def upload_avatar(file: UploadFile = File(...)):
    AVATAR_DIR.mkdir(parents=True, exist_ok=True)
    path = AVATAR_DIR / file.filename
    path.write_bytes(await file.read())
    if engine:
        await engine.load_avatar(str(path))
    return {"path": str(path), "name": file.filename}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    logger.info("Client connected")

    try:
        while True:
            raw = await ws.receive_text()
            msg = json.loads(raw)
            t = msg.get("type")

            if t == "ping":
                await ws.send_json({"type": "pong"})

            elif t == "avatar.load":
                try:
                    info = await engine.load_avatar(msg.get("model_path", ""))
                    await ws.send_json({"type": "avatar.ready", "info": info})
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})

            elif t == "list_agents":
                try:
                    oc = await get_openclaw()
                    agents = await oc.list_agents()
                    await ws.send_json({"type": "agents", "agents": agents})
                except Exception as e:
                    await ws.send_json({"type": "error", "message": f"OpenClaw: {e}"})

            elif t == "chat":
                agent_id = msg.get("agent", "vp-manager")
                user_text = msg.get("text", "")
                if not user_text:
                    continue

                try:
                    # 1. Send to OpenClaw agent
                    await ws.send_json({"type": "status", "message": f"Sending to {agent_id}..."})
                    oc = await get_openclaw()
                    result = await oc.send_to_agent(agent_id, user_text, timeout=30)
                    agent_text = result.get("text", "")

                    if not agent_text:
                        await ws.send_json({"type": "agent.text", "text": "(no response from agent)"})
                        continue

                    await ws.send_json({"type": "agent.text", "text": agent_text, "agent": agent_id})
                    logger.info(f"Agent [{agent_id}]: {agent_text[:80]}")

                    # 2. TTS
                    await ws.send_json({"type": "status", "message": "Generating speech..."})
                    pcm, sr = synthesize(agent_text)
                    logger.info(f"TTS: {len(pcm)/2/sr:.1f}s at {sr}Hz")

                    # 3. Engine → animation
                    await ws.send_json({"type": "status", "message": "Animating..."})
                    audio_f32 = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                    result = await engine.process_batch(audio_f32, sample_rate=sr)
                    frames = result.get("frames", [])
                    logger.info(f"Animation: {len(frames)} frames in {result.get('compute_ms',0)}ms")

                    # 4. Send batch to browser
                    batch = []
                    for f in frames:
                        batch.append({
                            "w": f.get("w", {}),
                            "h": f.get("h", {}),
                            "v": f.get("v", "REST"),
                            "s": f.get("s", False),
                        })
                    audio_b64 = base64.b64encode(pcm).decode()
                    await ws.send_json({
                        "type": "batch_weights",
                        "frames": batch,
                        "audio_b64": audio_b64,
                        "sample_rate": sr,
                        "duration": len(pcm) / 2 / sr,
                    })

                except Exception as e:
                    logger.error(f"Chat error: {e}", exc_info=True)
                    await ws.send_json({"type": "error", "message": str(e)})

            elif t == "audio.batch":
                # Direct audio processing (upload audio file)
                try:
                    pcm_bytes = base64.b64decode(msg.get("data", ""))
                    sr = msg.get("sample_rate", 16000)
                    audio_f32 = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    result = await engine.process_batch(audio_f32, sample_rate=sr)
                    frames = result.get("frames", [])
                    await ws.send_json({
                        "type": "batch_weights",
                        "frames": [{"w":f.get("w",{}),"h":f.get("h",{}),"v":f.get("v","REST"),"s":f.get("s",False)} for f in frames],
                        "duration": result.get("duration", 0),
                        "compute_ms": result.get("compute_ms", 0),
                    })
                except Exception as e:
                    await ws.send_json({"type": "error", "message": str(e)})

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WS error: {e}")


@app.get("/")
async def index():
    return HTMLResponse(UI_HTML)


UI_HTML = r"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>Clawvatar — Agent Video Call</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,sans-serif;background:#08080e;color:#ddd;height:100vh;display:flex;flex-direction:column}
.top{background:#10101a;padding:8px 16px;display:flex;align-items:center;gap:12px;border-bottom:1px solid #1a1a2a}
.top h1{font-size:14px;color:#fff;flex:1}.top h1 b{color:#7c6ef0}
.badge{font-size:10px;padding:2px 8px;border-radius:8px;font-weight:600}
.badge.off{background:#2a1515;color:#f66}.badge.on{background:#152a15;color:#6f6}
.badge.oc{background:#15152a;color:#88f}
.main{flex:1;display:flex;overflow:hidden}
#view{flex:1;background:#0a0a12;position:relative}
canvas{width:100%;height:100%;display:block}
.hud{position:absolute;top:8px;left:8px;background:rgba(0,0,0,.6);padding:4px 8px;border-radius:6px;font:10px/1.5 monospace;color:#555;pointer-events:none}
.hud b{color:#6f6}
.side{width:380px;background:#10101a;border-left:1px solid #1a1a2a;display:flex;flex-direction:column}
.agent-bar{padding:8px 12px;border-bottom:1px solid #1a1a2a;display:flex;align-items:center;gap:8px}
.agent-bar select{flex:1;background:#1a1a28;border:1px solid #2a2a3a;border-radius:6px;padding:6px;color:#ddd;font-size:12px}
.chat{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px}
.msg{padding:8px 12px;border-radius:10px;max-width:85%;font-size:12px;line-height:1.5;word-wrap:break-word}
.msg.user{background:#2a2a44;align-self:flex-end;border-bottom-right-radius:2px}
.msg.agent{background:#1a2a1a;align-self:flex-start;border-bottom-left-radius:2px}
.msg.sys{color:#555;font-size:10px;align-self:center;font-style:italic}
.bottom{padding:8px 12px;border-top:1px solid #1a1a2a}
.input-row{display:flex;gap:6px}
.input-row input{flex:1;background:#1a1a28;border:1px solid #2a2a3a;border-radius:8px;padding:8px 12px;color:#ddd;font-size:12px;outline:none}
.input-row input:focus{border-color:#7c6ef0}
.input-row button{background:#7c6ef0;border:none;border-radius:8px;padding:8px 16px;color:#fff;font-weight:700;cursor:pointer;font-size:12px}
.tools{display:flex;gap:6px;margin-top:6px}
.tools button{background:#222;border:none;border-radius:6px;padding:5px 10px;color:#888;font-size:10px;cursor:pointer}
.tools button:hover{background:#333}
input[type=file]{display:none}
</style>
</head><body>
<div class="top">
    <h1><b>Claw</b>vatar Agent Call</h1>
    <span id="ws_st" class="badge off">WS: off</span>
    <span id="oc_st" class="badge off">OC: off</span>
</div>
<div class="main">
    <div id="view"><canvas id="c"></canvas>
        <div class="hud"><b>FPS</b> <span id="fps">0</span> <b>Viseme</b> <span id="vis">-</span></div>
    </div>
    <div class="side">
        <div class="agent-bar">
            <select id="agentSel"><option value="">Select agent...</option></select>
            <button onclick="loadAgents()" style="background:#333;border:none;border-radius:6px;padding:5px 10px;color:#888;font-size:10px;cursor:pointer">Refresh</button>
        </div>
        <div class="chat" id="chat"></div>
        <div class="bottom">
            <div class="input-row">
                <input id="inp" placeholder="Type a message..." autofocus>
                <button onclick="send()">Send</button>
            </div>
            <div class="tools">
                <input type="file" id="fi" accept=".vrm,.glb">
                <button onclick="document.getElementById('fi').click()">Upload Avatar</button>
                <input type="file" id="af" accept=".wav,.mp3,.ogg">
                <button onclick="document.getElementById('af').click()">Upload Audio</button>
            </div>
        </div>
    </div>
</div>

<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.162.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.162.0/examples/jsm/"}}
</script>
<script type="module">
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
let VLP;try{VLP=(await import('https://cdn.jsdelivr.net/npm/@pixiv/three-vrm@3.1.1/lib/three-vrm.module.min.js')).VRMLoaderPlugin;}catch(e){}
const R=new THREE.WebGLRenderer({canvas:document.getElementById('c'),antialias:true});
R.setPixelRatio(Math.min(devicePixelRatio,2));R.outputColorSpace=THREE.SRGBColorSpace;R.toneMapping=THREE.ACESFilmicToneMapping;
const scene=new THREE.Scene();scene.background=new THREE.Color(0x0a0a12);
const cam=new THREE.PerspectiveCamera(20,1,.01,100);cam.position.set(0,1.35,.9);
const ctrl=new OrbitControls(cam,R.domElement);ctrl.target.set(0,1.3,0);ctrl.enableDamping=true;ctrl.update();
scene.add(new THREE.AmbientLight(0xffffff,.8));
const d1=new THREE.DirectionalLight(0xffffff,1.2);d1.position.set(2,3,2);scene.add(d1);
scene.add(new THREE.DirectionalLight(0x8888ff,.4)).position.set(-2,1,-1);
const loader=new GLTFLoader();if(VLP)loader.register(p=>new VLP(p));
function rsz(){const v=document.getElementById('view');R.setSize(v.clientWidth,v.clientHeight);cam.aspect=v.clientWidth/v.clientHeight;cam.updateProjectionMatrix();}
addEventListener('resize',rsz);rsz();
let vrm=null,clk=new THREE.Clock(),curW={},tgtW={},curH={y:0,p:0,r:0},tgtH={y:0,p:0,r:0},hb=null;
const NM={'blendShape2.mouth_a':'aa','blendShape2.mouth_i':'ih','blendShape2.mouth_u':'ou','blendShape2.mouth_e':'ee','blendShape2.mouth_o':'oh','blendShape2.Blink_L':'blinkLeft','blendShape2.Blink_R':'blinkRight','blendShape2.happy':'happy','blendShape2.angry':'angry','blendShape2.sorrow':'sad','blendShape2.joy':'surprised','happy':'happy','sad':'sad','relaxed':'relaxed','surprised':'surprised','blinkLeft':'blinkLeft','blinkRight':'blinkRight'};
function lerp(a,b,t){return a+(b-a)*t}
let fc=0,ft=performance.now();
function anim(){
    requestAnimationFrame(anim);ctrl.update();
    for(const k of new Set([...Object.keys(curW),...Object.keys(tgtW)])){const c=curW[k]||0,t=tgtW[k]||0,v=lerp(c,t,.25);if(v>.003)curW[k]=v;else delete curW[k];}
    curH.y=lerp(curH.y,tgtH.y,.12);curH.p=lerp(curH.p,tgtH.p,.12);curH.r=lerp(curH.r,tgtH.r,.12);
    if(vrm){
        const em=vrm.expressionManager;if(em){for(const n of Object.keys(em.expressionMap))try{em.setValue(n,0)}catch(e){}for(const[k,v] of Object.entries(curW))try{em.setValue(NM[k]||k,Math.min(1,v))}catch(e){}}
        if(hb){hb.rotation.y=curH.y*Math.PI/180;hb.rotation.x=curH.p*Math.PI/180;hb.rotation.z=curH.r*Math.PI/180;}
        vrm.update(clk.getDelta());
    }
    R.render(scene,cam);fc++;if(performance.now()-ft>=1e3){document.getElementById('fps').textContent=fc;fc=0;ft=performance.now();}
}
anim();
document.getElementById('fi').onchange=async function(){
    if(!this.files.length)return;const f=this.files[0],u=URL.createObjectURL(f);
    if(vrm){scene.remove(vrm.scene);vrm=null;hb=null;}
    try{const g=await loader.loadAsync(u);vrm=g.userData.vrm;if(vrm){scene.add(vrm.scene);cam.position.set(0,1.35,.9);ctrl.target.set(0,1.3,0);ctrl.update();try{const h=vrm.humanoid;if(h){const la=h.getNormalizedBoneNode('leftUpperArm'),ra=h.getNormalizedBoneNode('rightUpperArm');if(la)la.rotation.z=1.2;if(ra)ra.rotation.z=-1.2;hb=h.getNormalizedBoneNode('head');}}catch(e){}addMsg('Avatar: '+f.name,'sys');const fd=new FormData();fd.append('file',f);await fetch(location.protocol+'//'+location.host+'/upload',{method:'POST',body:fd});}}catch(e){addMsg('Error: '+e.message,'sys');}
    URL.revokeObjectURL(u);this.value='';
};
window._setTgt=function(w,h){tgtW=w;tgtH=h;};
</script>
<script>
var ws,isPlaying=false;
function addMsg(t,c){var d=document.createElement('div');d.className='msg '+(c||'agent');d.textContent=t;var ch=document.getElementById('chat');ch.appendChild(d);ch.scrollTop=99999;}

(function connect(){
    var p=location.protocol==='https:'?'wss:':'ws:';
    ws=new WebSocket(p+'//'+location.host+'/ws');
    ws.onopen=function(){document.getElementById('ws_st').className='badge on';document.getElementById('ws_st').textContent='WS: on';loadAgents();};
    ws.onclose=function(){document.getElementById('ws_st').className='badge off';document.getElementById('ws_st').textContent='WS: off';setTimeout(connect,3000);};
    ws.onmessage=function(e){
        try{var m=JSON.parse(e.data);
        if(m.type==='agents'){
            var s=document.getElementById('agentSel');s.innerHTML='';
            m.agents.forEach(function(a){var o=document.createElement('option');o.value=a.id;o.textContent=a.id;s.appendChild(o);});
            document.getElementById('oc_st').className='badge oc';document.getElementById('oc_st').textContent='OC: '+m.agents.length+' agents';
        }else if(m.type==='batch_weights'){playBatch(m);
        }else if(m.type==='agent.text'){addMsg('['+m.agent+'] '+m.text,'agent');
        }else if(m.type==='status'){addMsg(m.message,'sys');
        }else if(m.type==='error'){addMsg('Error: '+m.message,'sys');
        }else if(m.type==='avatar.ready'){addMsg('Avatar ready: '+(m.info&&m.info.name),'sys');
        }}catch(err){}
    };
})();

function loadAgents(){if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'list_agents'}));}

function send(){
    var inp=document.getElementById('inp'),text=inp.value.trim();
    var agent=document.getElementById('agentSel').value;
    if(!text||!ws||ws.readyState!==1)return;
    if(!agent){addMsg('Select an agent first','sys');return;}
    addMsg(text,'user');
    ws.send(JSON.stringify({type:'chat',text:text,agent:agent}));
    inp.value='';
}
document.getElementById('inp').onkeydown=function(e){if(e.key==='Enter')send();};

function playBatch(m){
    isPlaying=true;var frames=m.frames||[],ab64=m.audio_b64||'',sr=m.sample_rate||22050;
    if(!frames.length||!ab64){isPlaying=false;return;}
    var raw=atob(ab64),buf=new ArrayBuffer(raw.length),v=new Uint8Array(buf);
    for(var i=0;i<raw.length;i++)v[i]=raw.charCodeAt(i);
    var ctx=new AudioContext({sampleRate:sr}),pcm=new Int16Array(buf),abuf=ctx.createBuffer(1,pcm.length,sr),ch=abuf.getChannelData(0);
    for(var i=0;i<pcm.length;i++)ch[i]=pcm[i]/32768;
    var src=ctx.createBufferSource();src.buffer=abuf;src.connect(ctx.destination);
    var dur=pcm.length/sr,chunkDur=dur/frames.length,startT=ctx.currentTime;src.start(startT);
    var fi=0;
    function sync(){
        var el=ctx.currentTime-startT,tgt=Math.floor(el/chunkDur);
        if(tgt!==fi&&tgt<frames.length){fi=tgt;var f=frames[fi];window._setTgt(f.w||{},{y:(f.h&&f.h.yaw)||0,p:(f.h&&f.h.pitch)||0,r:(f.h&&f.h.roll)||0});if(f.v)document.getElementById('vis').textContent=f.v;}
        if(el<dur)requestAnimationFrame(sync);
        else{isPlaying=false;window._setTgt({},{y:0,p:0,r:0});document.getElementById('vis').textContent='-';}
    }
    sync();src.onended=function(){ctx.close();};
}

// Audio file upload
document.getElementById('af').onchange=async function(){
    if(!this.files.length||!ws||ws.readyState!==1)return;
    var f=this.files[0];addMsg('Processing: '+f.name,'sys');
    var buf=await f.arrayBuffer(),ctx=new AudioContext(),dec=await ctx.decodeAudioData(buf.slice(0));
    var native=dec.sampleRate,pcm=dec.getChannelData(0),ratio=native/16000,dsLen=Math.round(pcm.length/ratio),ds=new Float32Array(dsLen);
    for(var i=0;i<dsLen;i++){var idx=i*ratio,lo=Math.floor(idx),hi=Math.min(lo+1,pcm.length-1),fr=idx-lo;ds[i]=pcm[lo]*(1-fr)+pcm[hi]*fr;}
    var i16=new Int16Array(ds.length);for(var j=0;j<ds.length;j++)i16[j]=Math.max(-32768,Math.min(32767,Math.round(ds[j]*32768)));
    var bytes=new Uint8Array(i16.buffer),bin='';for(var j=0;j<bytes.length;j++)bin+=String.fromCharCode(bytes[j]);
    ws.send(JSON.stringify({type:'audio.batch',data:btoa(bin),sample_rate:16000,chunk_size:1024}));
    // Play original audio when batch comes back
    var pb=await ctx.decodeAudioData(buf.slice(0)),src2=ctx.createBufferSource();src2.buffer=pb;src2.connect(ctx.destination);
    // Wait for batch response, then sync play
    var origOnMsg=ws.onmessage;
    ws.onmessage=function(e){var m=JSON.parse(e.data);if(m.type==='batch_weights'){src2.start();m.audio_b64='';playBatch(m);ws.onmessage=origOnMsg;addMsg('Playing '+f.name,'sys');}else if(origOnMsg)origOnMsg(e);};
    this.value='';
};

setInterval(function(){if(ws&&ws.readyState===1)ws.send(JSON.stringify({type:'ping'}));},15000);
</script>
</body></html>
"""

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8766,
        ssl_certfile="/home/vaibhav/openclaw-vaibhav.tail72d21d.ts.net.crt",
        ssl_keyfile="/tmp/ts-ssl.key", log_level="info")
