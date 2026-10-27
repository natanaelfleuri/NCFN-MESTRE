// Símbolo NCFN: o "rosto" do MESTRE.
//
// Emblema holográfico em camadas: grade hexagonal, cristal geodésico 3D
// girando, anéis orbitais com nós de dados, trilhas de circuito com pulsos e,
// no centro, o monograma NCFN desenhado em vetor. A fala faz o núcleo pulsar
// e soltar picos radiais; cada emoção muda velocidade, brilho, interferência
// e forma. Duas variantes: "angular" (hexagonal, cristalina) e "fluida"
// (pétalas e curvas). A interface é a mesma do antigo rosto: setMouth,
// setExpression, setColor, setEnergy e setGender.

const MOODS = {
  neutro: {},
  sorriso: { speed: 1.2, glow: 1.15, bounce: 0.3 },
  feliz: { speed: 1.5, glow: 1.3, bounce: 0.6, bloom: 0.25 },
  empolgado: { speed: 2.2, glow: 1.45, bounce: 0.9, bloom: 0.45, spikes: 0.25 },
  confiante: { speed: 1.1, glow: 1.25, steady: 1 },
  ironico: { speed: 1.2, wobble: 0.6, tilt: 0.25 },
  pensativo: { speed: 0.45, glow: 0.9, inward: 1, scan: 1 },
  curioso: { speed: 1.0, tilt: 0.35, scan: 0.6, bloom: 0.1 },
  surpreso: { speed: 1.6, bloom: 0.6, glow: 1.4 },
  preocupado: { speed: 0.8, glitch: 0.35, glow: 0.95, shrink: 0.08 },
  triste: { speed: 0.35, glow: 0.7, shrink: 0.14, droop: 1 },
  determinado: { speed: 1.8, glow: 1.2, spikes: 0.15, steady: 1 },
  serio: { speed: 0.7, glow: 1.0, steady: 1 },
  alerta: { speed: 2.4, glitch: 0.7, spikes: 0.45, glow: 1.35 },
  ouvindo: { speed: 0.9, glow: 1.15, inward: 0.5, bloom: 0.1 },
};
const BASE = { speed: 1, glow: 1, bounce: 0, bloom: 0, spikes: 0, wobble: 0, tilt: 0, inward: 0, scan: 0, glitch: 0, shrink: 0, droop: 0, steady: 0 };

// Monograma NCFN em segmentos (unidades de uma caixa 0..1 por letra).
const GLYPHS = {
  N: [[[0, 1], [0, 0], [1, 1], [1, 0]]],
  C: [[[1, 0.12], [0.85, 0], [0.15, 0], [0, 0.15], [0, 0.85], [0.15, 1], [0.85, 1], [1, 0.88]]],
  F: [[[1, 0], [0, 0], [0, 1]], [[0, 0.5], [0.72, 0.5]]],
};

function geodesic() {
  // Icosaedro subdividido uma vez, projetado na esfera.
  const t = (1 + Math.sqrt(5)) / 2;
  let v = [[-1, t, 0], [1, t, 0], [-1, -t, 0], [1, -t, 0], [0, -1, t], [0, 1, t], [0, -1, -t], [0, 1, -t], [t, 0, -1], [t, 0, 1], [-t, 0, -1], [-t, 0, 1]];
  let f = [[0, 11, 5], [0, 5, 1], [0, 1, 7], [0, 7, 10], [0, 10, 11], [1, 5, 9], [5, 11, 4], [11, 10, 2], [10, 7, 6], [7, 1, 8], [3, 9, 4], [3, 4, 2], [3, 2, 6], [3, 6, 8], [3, 8, 9], [4, 9, 5], [2, 4, 11], [6, 2, 10], [8, 6, 7], [9, 8, 1]];
  const norm = (p) => { const l = Math.hypot(...p); return p.map((c) => c / l); };
  v = v.map(norm);
  const cache = new Map();
  const mid = (a, b) => {
    const k = a < b ? `${a}_${b}` : `${b}_${a}`;
    if (!cache.has(k)) { v.push(norm(v[a].map((c, i) => (c + v[b][i]) / 2))); cache.set(k, v.length - 1); }
    return cache.get(k);
  };
  const f2 = [];
  for (const [a, b, c] of f) {
    const ab = mid(a, b), bc = mid(b, c), ca = mid(c, a);
    f2.push([a, ab, ca], [b, bc, ab], [c, ca, bc], [ab, bc, ca]);
  }
  const edges = new Set();
  for (const [a, b, c] of f2) for (const [x, y] of [[a, b], [b, c], [c, a]]) edges.add(x < y ? `${x}_${y}` : `${y}_${x}`);
  return { v, edges: [...edges].map((e) => e.split('_').map(Number)) };
}

export function createFace(canvas) {
  const ctx = canvas.getContext('2d');
  const geo = geodesic();
  const m = { ...BASE };
  let target = { ...BASE };
  const st = {
    amp: 0, ampS: 0, burst: 0, prevAmp: 0,
    color: [0, 229, 255], targetColor: [0, 229, 255], energy: 0,
    variant: 'angular', t0: performance.now(), rot: 0, ringRot: 0,
    pulses: [], look: { x: 0, y: 0 }, lookS: { x: 0, y: 0 },
  };
  const TRACES = Array.from({ length: 18 }, (_, i) => {
    // trilhas de circuito: saem do anel em ângulos fixos, com uma ou duas quebras
    const a = (i / 18) * Math.PI * 2 + (i % 2) * 0.08;
    const bend = (i % 3 === 0 ? 1 : -1) * (0.12 + (i % 4) * 0.04);
    return { a, bend, len: 0.34 + (i % 5) * 0.05 };
  });

  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const r = canvas.getBoundingClientRect();
    canvas.width = Math.round(r.width * dpr);
    canvas.height = Math.round(r.height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', resize);
  resize();
  window.addEventListener('mousemove', (e) => {
    st.look.x = (e.clientX / window.innerWidth - 0.5) * 2;
    st.look.y = (e.clientY / window.innerHeight - 0.5) * 2;
  });
  const rgba = (a, boost = 0) => {
    const [r, g, b] = st.color;
    return `rgba(${Math.min(255, r + boost) | 0},${Math.min(255, g + boost) | 0},${Math.min(255, b + boost) | 0},${Math.max(0, Math.min(1, a))})`;
  };

  function frame(now) {
    try {
      draw(now);
    } catch (e) {
      console.warn('[símbolo]', e);
      ctx.globalCompositeOperation = 'source-over';
      ctx.shadowBlur = 0;
    }
    requestAnimationFrame(frame);
  }

  function draw(now) {
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;
    if (!W || !H) return;
    ctx.clearRect(0, 0, W, H);
    const t = (now - st.t0) / 1000;
    for (const k of Object.keys(BASE)) {
      m[k] += ((Number.isFinite(target[k]) ? target[k] : BASE[k]) - m[k]) * 0.05;
      if (!Number.isFinite(m[k])) m[k] = BASE[k];
    }
    if (!Number.isFinite(st.energy)) st.energy = 0;
    if (!Number.isFinite(st.amp)) st.amp = 0;
    for (let i = 0; i < 3; i++) st.color[i] += (st.targetColor[i] - st.color[i]) * 0.06;
    st.ampS += (st.amp - st.ampS) * 0.4;
    if (st.amp - st.prevAmp > 0.3) st.burst = Math.min(1, st.burst + 0.6);
    st.prevAmp = st.amp;
    st.burst *= 0.92;
    st.lookS.x += (st.look.x - st.lookS.x) * 0.04;
    st.lookS.y += (st.look.y - st.lookS.y) * 0.04;

    const fluid = st.variant === 'fluida';
    const R = Math.min(W, H) * 0.42 * (1 - m.shrink) * (1 + Math.sin(t * 1.3) * 0.01 + m.bounce * Math.max(0, Math.sin(t * 3)) * 0.03);
    const glitchX = m.glitch > 0.05 && Math.random() < m.glitch * 0.25 ? (Math.random() - 0.5) * m.glitch * 18 : 0;
    const cx = W / 2 + glitchX;
    const cy = H / 2 + m.droop * R * 0.05;
    const speed = m.speed * (1 + st.energy * 0.6);
    st.rot += 0.004 * speed;
    st.ringRot += 0.006 * speed * (m.inward ? -1 : 1);
    const amp = st.ampS;
    const glow = m.glow * (1 + amp * 0.6 + st.energy * 0.2);

    ctx.globalCompositeOperation = 'lighter';
    ctx.lineCap = ctx.lineJoin = 'round';

    // ---------- grade hexagonal ao fundo
    const hex = R * 0.16;
    for (let q = -5; q <= 5; q++) {
      for (let r = -5; r <= 5; r++) {
        const x = cx + hex * 1.5 * q;
        const y = cy + hex * Math.sqrt(3) * (r + q / 2);
        const d = Math.hypot(x - cx, y - cy) / (R * 1.35);
        if (d > 1 || d < 0.62) continue;
        const a = (1 - d) * 0.22 * (0.6 + 0.4 * Math.sin(t * 2 + q * 0.7 + r));
        ctx.beginPath();
        for (let i = 0; i <= 6; i++) {
          const an = (Math.PI / 3) * i;
          const px = x + Math.cos(an) * hex * 0.5;
          const py = y + Math.sin(an) * hex * 0.5;
          i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
        }
        ctx.strokeStyle = rgba(a);
        ctx.lineWidth = 1;
        ctx.stroke();
      }
    }

    // ---------- trilhas de circuito com pulsos de dados
    const ringR = R * 0.62;
    for (const [i, tr] of TRACES.entries()) {
      const a0 = tr.a + st.ringRot * 0.15;
      const p0 = [cx + Math.cos(a0) * ringR, cy + Math.sin(a0) * ringR];
      const a1 = a0 + tr.bend;
      const r1 = ringR + R * tr.len * 0.55;
      const p1 = [cx + Math.cos(a0) * r1, cy + Math.sin(a0) * r1];
      const p2 = [cx + Math.cos(a1) * (r1 + R * tr.len * 0.45), cy + Math.sin(a1) * (r1 + R * tr.len * 0.45)];
      ctx.beginPath();
      ctx.moveTo(...p0);
      ctx.lineTo(...p1);
      ctx.lineTo(...p2);
      ctx.strokeStyle = rgba(0.18 + amp * 0.3);
      ctx.lineWidth = 1.2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(...p2, 2.4, 0, Math.PI * 2);
      ctx.fillStyle = rgba(0.6);
      ctx.fill();
      // pulso correndo pela trilha (para fora ao falar, para dentro ao pensar)
      const phase = (t * (0.35 + speed * 0.25) + i * 0.37) % 1;
      const k = m.inward > 0.5 ? 1 - phase : phase;
      const seg = k < 0.55 ? [p0, p1, k / 0.55] : [p1, p2, (k - 0.55) / 0.45];
      const px = seg[0][0] + (seg[1][0] - seg[0][0]) * seg[2];
      const py = seg[0][1] + (seg[1][1] - seg[0][1]) * seg[2];
      ctx.beginPath();
      ctx.arc(px, py, 2 + amp * 3, 0, Math.PI * 2);
      ctx.fillStyle = rgba(0.9, 80);
      ctx.shadowColor = rgba(1);
      ctx.shadowBlur = 12;
      ctx.fill();
      ctx.shadowBlur = 0;
    }

    // ---------- anel de voz: picos radiais com o volume
    const N = fluid ? 120 : 72;
    ctx.beginPath();
    for (let i = 0; i <= N; i++) {
      const a = (i / N) * Math.PI * 2 + st.rot * 0.5;
      const wave = fluid
        ? Math.sin(a * 6 + t * 2) * 0.04 + Math.sin(a * 3 - t * 1.3) * 0.03
        : (i % 2 ? 0.035 : -0.01);
      const spike = (amp * 0.22 + m.spikes * 0.08 + st.burst * 0.12) * Math.abs(Math.sin(a * (fluid ? 5 : 9) + t * 7));
      const r = ringR * (1 + wave * (0.6 + amp) + spike);
      const x = cx + Math.cos(a) * r;
      const y = cy + Math.sin(a) * r;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = rgba(0.75 * glow, 40);
    ctx.lineWidth = 2;
    ctx.shadowColor = rgba(1);
    ctx.shadowBlur = 18;
    ctx.stroke();
    ctx.shadowBlur = 0;

    // ---------- anéis orbitais 3D com nós
    const tiltX = 1.1 + m.tilt * 0.5 + st.lookS.y * 0.15;
    for (let k = 0; k < (fluid ? 4 : 3); k++) {
      const yaw = st.ringRot * (k % 2 ? -1 : 1) * (1 + k * 0.3) + (k * Math.PI) / 3 + m.wobble * Math.sin(t * 2 + k) * 0.4;
      const rr = R * (0.5 + k * 0.045);
      const pts = [];
      for (let i = 0; i <= 90; i++) {
        const a = (i / 90) * Math.PI * 2;
        let x = Math.cos(a) * rr;
        let y = Math.sin(a) * rr;
        let z = 0;
        // inclina o anel e gira em torno do eixo vertical
        [y, z] = [y * Math.cos(tiltX), y * Math.sin(tiltX)];
        [x, z] = [x * Math.cos(yaw) - z * Math.sin(yaw), x * Math.sin(yaw) + z * Math.cos(yaw)];
        pts.push([cx + x, cy + y, z]);
      }
      ctx.beginPath();
      pts.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.strokeStyle = rgba(0.16 + 0.1 * glow);
      ctx.lineWidth = 1;
      ctx.stroke();
      for (let j = 0; j < 3; j++) {
        const ph = (t * 0.12 * (Number.isFinite(speed) ? speed : 1) * (k + 1) + j / 3) % 1;
        const [x, y, z] = pts[Math.min(90, Math.max(0, Math.floor((ph + 1) % 1 * 90)))];
        ctx.beginPath();
        ctx.arc(x, y, 2.2 + (z > 0 ? 1.5 : 0) + amp * 2, 0, Math.PI * 2);
        ctx.fillStyle = rgba(z > 0 ? 0.95 : 0.4, 60);
        ctx.shadowColor = rgba(1);
        ctx.shadowBlur = z > 0 ? 14 : 4;
        ctx.fill();
      }
      ctx.shadowBlur = 0;
    }

    // ---------- cristal geodésico (angular) ou flor de curvas (fluida)
    const coreR = R * (0.33 + amp * 0.07 + st.burst * 0.05 + m.bloom * 0.06);
    const ry = st.rot * 2 + st.lookS.x * 0.4;
    const rx = st.rot * 1.3 + st.lookS.y * 0.3;
    const project = ([x, y, z]) => {
      [x, z] = [x * Math.cos(ry) - z * Math.sin(ry), x * Math.sin(ry) + z * Math.cos(ry)];
      [y, z] = [y * Math.cos(rx) - z * Math.sin(rx), y * Math.sin(rx) + z * Math.cos(rx)];
      const p = 3 / (3 - z);
      return [cx + x * coreR * p, cy + y * coreR * p, z];
    };
    if (!fluid) {
      const P = geo.v.map((v) => {
        const n = 1 + (m.spikes + amp * 0.4) * 0.25 * Math.max(0, Math.sin(v[0] * 7 + v[1] * 5 + t * 4));
        return project(v.map((c) => c * n));
      });
      for (const [a, b] of geo.edges) {
        const za = (P[a][2] + P[b][2]) / 2;
        ctx.beginPath();
        ctx.moveTo(P[a][0], P[a][1]);
        ctx.lineTo(P[b][0], P[b][1]);
        ctx.strokeStyle = rgba((0.12 + (za + 1) * 0.16) * glow, 30);
        ctx.lineWidth = za > 0 ? 1.3 : 0.8;
        ctx.stroke();
      }
      for (const p of P) {
        if (p[2] < 0.2) continue;
        ctx.fillStyle = rgba(0.5 * glow, 80);
        ctx.fillRect(p[0] - 1, p[1] - 1, 2, 2);
      }
    } else {
      for (let k = 0; k < 6; k++) {
        ctx.beginPath();
        for (let i = 0; i <= 120; i++) {
          const a = (i / 120) * Math.PI * 2;
          const r = 0.55 + 0.45 * Math.cos(3 * a) * (0.8 + amp * 0.4 + m.bloom * 0.3);
          const v = [Math.cos(a) * r, Math.sin(a) * r, Math.sin(a * 3 + k) * 0.25];
          const rot = (k / 6) * Math.PI;
          const [x, y, z] = project([v[0] * Math.cos(rot) - v[2] * Math.sin(rot), v[1], v[0] * Math.sin(rot) + v[2] * Math.cos(rot)]);
          i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
        }
        ctx.strokeStyle = rgba(0.22 * glow, 30);
        ctx.lineWidth = 1.1;
        ctx.stroke();
      }
    }

    // ---------- núcleo: hexágono (ou círculo) com brilho e o monograma NCFN
    const core = R * 0.2 * (1 + amp * 0.25 + st.burst * 0.15);
    const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, core * 2.2);
    grad.addColorStop(0, rgba(0.55 * glow, 90));
    grad.addColorStop(0.45, rgba(0.18 * glow, 40));
    grad.addColorStop(1, rgba(0));
    ctx.fillStyle = grad;
    ctx.fillRect(cx - core * 2.2, cy - core * 2.2, core * 4.4, core * 4.4);
    ctx.beginPath();
    for (let i = 0; i <= 6; i++) {
      const a = (Math.PI / 3) * i + Math.PI / 6 + (fluid ? 0 : st.rot * 0.5);
      const r = fluid ? core * 1.25 : core * 1.25;
      const x = cx + Math.cos(a) * r;
      const y = cy + Math.sin(a) * r;
      if (fluid) {
        if (i === 0) ctx.moveTo(cx + r, cy);
      } else {
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      }
    }
    if (fluid) ctx.arc(cx, cy, core * 1.25, 0, Math.PI * 2);
    ctx.strokeStyle = rgba(0.9 * glow, 60);
    ctx.lineWidth = 2;
    ctx.shadowColor = rgba(1);
    ctx.shadowBlur = 20;
    ctx.stroke();
    // segmentos girando em volta do núcleo
    for (let i = 0; i < 6; i++) {
      const a = st.ringRot * 2 + (i * Math.PI) / 3;
      ctx.beginPath();
      ctx.arc(cx, cy, core * 1.55, a, a + 0.55 + amp * 0.4);
      ctx.strokeStyle = rgba(0.7 * glow, 40);
      ctx.lineWidth = 2.5;
      ctx.stroke();
    }
    // monograma
    const letters = 'NCFN';
    const lw = core * 0.36;
    const lh = core * 0.5;
    const gap = core * 0.12;
    const total = letters.length * lw + (letters.length - 1) * gap;
    ctx.lineWidth = Math.max(1.6, core * 0.07);
    ctx.strokeStyle = rgba(1, 140);
    ctx.shadowBlur = 16;
    [...letters].forEach((ch, i) => {
      const ox = cx - total / 2 + i * (lw + gap);
      const oy = cy - lh / 2;
      // interferência: letras saltam um pouco em alerta/preocupado
      const jx = m.glitch > 0.2 && Math.random() < m.glitch * 0.1 ? (Math.random() - 0.5) * 6 : 0;
      for (const strokePts of GLYPHS[ch]) {
        ctx.beginPath();
        strokePts.forEach(([x, y], k) => {
          const px = ox + x * lw + jx;
          const py = oy + y * lh;
          k ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
        });
        ctx.stroke();
      }
    });
    ctx.shadowBlur = 0;

    // ---------- varredura (pensativo/curioso): feixe girando
    if (m.scan > 0.05) {
      const a = t * 2.2;
      const g2 = ctx.createConicGradient ? ctx.createConicGradient(a, cx, cy) : null;
      if (g2) {
        g2.addColorStop(0, rgba(0.28 * m.scan));
        g2.addColorStop(0.08, rgba(0));
        g2.addColorStop(1, rgba(0));
        ctx.fillStyle = g2;
        ctx.beginPath();
        ctx.arc(cx, cy, ringR, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    ctx.globalCompositeOperation = 'source-over';
  }
  requestAnimationFrame(frame);

  return {
    setMouth(v) { st.amp = Math.max(0, Math.min(1, v)); },
    setColor(rgb) { st.targetColor = rgb; },
    setEnergy(v) { st.energy = v; },
    setGender(g) { st.variant = g === 'feminino' ? 'fluida' : 'angular'; },
    setExpression(name) { target = { ...BASE, ...(MOODS[name] || {}) }; },
    expressions: Object.keys(MOODS),
  };
}
