/* Procedural WebGL highlights for the web approximation; controls stay usable without WebGL. */
(() => {
  const selector = '.open-chat, .chat-close, .login-form button:not(.chat-close), #chat-send, .logout';
  const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
  const reducedTransparency = matchMedia('(prefers-reduced-transparency: reduce)');
  const forcedColors = matchMedia('(forced-colors: active)');
  // A normal map bends the backdrop near the edges of each lens.
  const map = document.createElement('canvas'); map.width = 128; map.height = 64;
  const mapContext = map.getContext('2d');
  if (mapContext) {
    const pixels = mapContext.createImageData(map.width, map.height);
    for (let y = 0; y < map.height; y++) for (let x = 0; x < map.width; x++) {
      const nx = (x / (map.width-1) - .5) * 2, ny = (y / (map.height-1) - .5) * 2;
      const edgeX = Math.pow(Math.abs(nx), 5) * Math.sign(nx);
      const edgeY = Math.pow(Math.abs(ny), 5) * Math.sign(ny);
      const i = (y*map.width+x)*4;
      pixels.data[i] = 128 + edgeX*100; pixels.data[i+1] = 128 + edgeY*100; pixels.data[i+2] = 128; pixels.data[i+3] = 255;
    }
    mapContext.putImageData(pixels,0,0);
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns,'svg'); svg.setAttribute('aria-hidden','true'); svg.classList.add('glass-filter');
    const filter = document.createElementNS(ns,'filter'); filter.id = 'glass-distortion'; filter.setAttribute('color-interpolation-filters','sRGB');
    const image = document.createElementNS(ns,'feImage'); image.setAttribute('href',map.toDataURL()); image.setAttribute('preserveAspectRatio','none'); image.setAttribute('result','normal');
    const displacement = document.createElementNS(ns,'feDisplacementMap'); displacement.setAttribute('in','SourceGraphic'); displacement.setAttribute('in2','normal'); displacement.setAttribute('scale','12'); displacement.setAttribute('xChannelSelector','R'); displacement.setAttribute('yChannelSelector','G');
    filter.append(image,displacement); svg.append(filter); document.body.append(svg);
    document.documentElement.classList.add('glass-refraction');
  }
  const source = document.createElement('canvas');
  const gl = source.getContext('webgl', {alpha: true, premultipliedAlpha: false, antialias: true});
  if (!gl) return;
  const vertex = 'attribute vec2 p; void main(){gl_Position=vec4(p,0.,1.);}';
  const fragment = `
    precision highp float;
    uniform vec2 size;
    uniform vec2 mouse;
    uniform float clock;
    uniform float hover;
    uniform float dark;
    float capsule(vec2 p, vec2 b, float r) {
      vec2 q = abs(p) - b + r;
      return length(max(q,0.0)) + min(max(q.x,q.y),0.0) - r;
    }
    void main() {
      vec2 p = gl_FragCoord.xy;
      vec2 center = size * .5;
      float d = capsule(p-center, center-1.0, center.y-1.0);
      float mask = 1.0-smoothstep(-1.2,1.0,d);
      float inside = max(-d,0.0);
      float rim = exp(-inside * .48);
      float bevel = exp(-pow((inside-3.0)/3.4,2.0));
      float y = p.y/size.y;
      float x = p.x/size.x;
      float top = smoothstep(.40,.95,y);
      float bottom = 1.0-smoothstep(.02,.42,y);
      float side = pow(abs(x*2.0-1.0),3.0);
      float beam = exp(-pow((x-(.5+.40*sin(clock*.72)))/.17,2.0));
      float sweep = exp(-pow((x + y*.24 - (.55+.55*sin(clock*.49+1.2)))/.13,2.0));
      float moving = exp(-pow(length((p-mouse)/vec2(max(size.y*2.5,1.0),size.y*1.8)),2.0)*2.0) * hover;
      float spark = pow(max(0.0,1.0-length((p-mouse)/vec2(size.y*.75,size.y*.65))),2.0)*hover;
      float light = rim*(.13 + .26*top + .16*side + .36*moving)
                  + bevel*(.04 + .23*top*beam + .30*moving)
                  + .24*top*beam + .15*sweep + .20*spark + .04*bottom;
      light *= mix(.86, 1.10, dark);
      vec3 tint = mix(vec3(.76,.87,1.0),vec3(1.0),clamp(top + moving,0.0,1.0));
      gl_FragColor = vec4(tint, clamp(light,0.0,.68)*mask);
    }`;
  function shader(type, code) {
    const s = gl.createShader(type); gl.shaderSource(s, code); gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  let program;
  try {
    program = gl.createProgram(); gl.attachShader(program, shader(gl.VERTEX_SHADER, vertex));
    gl.attachShader(program, shader(gl.FRAGMENT_SHADER, fragment)); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) return;
  } catch { return; }
  gl.useProgram(program);
  const buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, 1,-1, -1,1, -1,1, 1,-1, 1,1]), gl.STATIC_DRAW);
  const position = gl.getAttribLocation(program, 'p'); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0);
  const uniforms = Object.fromEntries(['size','mouse','clock','hover','dark'].map(name => [name, gl.getUniformLocation(program,name)]));
  const canvases = new WeakMap();
  const buttons = new Set();
  const darkScheme = matchMedia('(prefers-color-scheme: dark)');
  // One shared drawing buffer avoids creating a GPU context for each button.
  source.width = 1024; source.height = 256;
  gl.enable(gl.SCISSOR_TEST);
  let point = null, frame = 0, lastDraw = 0;
  function draw(button, time = performance.now()) {
    const canvas = canvases.get(button);
    if (!canvas || !button.isConnected || reducedTransparency.matches || forcedColors.matches) return;
    const rect = button.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    if (rect.bottom < 0 || rect.top > innerHeight || rect.right < 0 || rect.left > innerWidth) return;
    const ratio = Math.min(devicePixelRatio || 1, 2, source.width/rect.width, source.height/rect.height);
    const w = Math.max(1, Math.round(rect.width*ratio)), h = Math.max(1, Math.round(rect.height*ratio));
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; }
    gl.viewport(0,0,w,h); gl.scissor(0,0,w,h); gl.clearColor(0,0,0,0); gl.clear(gl.COLOR_BUFFER_BIT);
    gl.uniform2f(uniforms.size,w,h);
    gl.uniform2f(uniforms.mouse, point ? (point.x-rect.left)*ratio : -1000, point ? (rect.bottom-point.y)*ratio : -1000);
    const dx = point ? Math.max(rect.left-point.x, 0, point.x-rect.right) : 1000;
    const dy = point ? Math.max(rect.top-point.y, 0, point.y-rect.bottom) : 1000;
    gl.uniform1f(uniforms.clock,reducedMotion.matches ? 0 : time/1000 + rect.left/320 + rect.top/240);
    gl.uniform1f(uniforms.hover,Math.max(0,1-Math.hypot(dx,dy)/150));
    gl.uniform1f(uniforms.dark,darkScheme.matches ? 1 : 0);
    gl.drawArrays(gl.TRIANGLES,0,6);
    const ctx = canvas.getContext('2d'); ctx.clearRect(0,0,w,h);
    ctx.drawImage(source,0,source.height-h,w,h,0,0,w,h);
  }
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(entries => {
    for (const entry of entries) draw(entry.target);
  }) : null;
  function attach() {
    for (const button of buttons) {
      if (!button.isConnected) { resizeObserver?.unobserve(button); buttons.delete(button); }
    }
    document.querySelectorAll(selector).forEach(button => {
      if (canvases.has(button)) return;
      const canvas = document.createElement('canvas'); canvas.className = 'glass-light'; canvas.setAttribute('aria-hidden','true');
      button.prepend(canvas); canvases.set(button,canvas); buttons.add(button); resizeObserver?.observe(button); draw(button);
    });
  }
  function drawAll(time) {
    for (const button of buttons) {
      if (!button.isConnected) { resizeObserver?.unobserve(button); buttons.delete(button); }
      else draw(button,time);
    }
  }
  function shouldAnimate() {
    return !document.hidden && !reducedMotion.matches && !reducedTransparency.matches && !forcedColors.matches;
  }
  function tick(time) {
    frame = 0;
    if (!shouldAnimate()) return;
    if (time - lastDraw >= 40) { drawAll(time); lastDraw = time; }
    frame = requestAnimationFrame(tick);
  }
  function updateAnimation() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    if (shouldAnimate()) frame = requestAnimationFrame(tick);
    else drawAll(performance.now());
  }
  document.addEventListener('pointermove', event => {
    point = {x:event.clientX,y:event.clientY};
    if (reducedMotion.matches) drawAll(performance.now());
  }, {passive:true});
  document.addEventListener('pointerout', event => {
    if (event.relatedTarget) return;
    point = null;
    if (reducedMotion.matches) drawAll(performance.now());
  });
  const refresh = () => { attach(); drawAll(performance.now()); };
  new MutationObserver(attach).observe(document.body,{childList:true,subtree:true});
  window.addEventListener('resize',refresh);
  window.addEventListener('scroll', () => { if (reducedMotion.matches) drawAll(performance.now()); }, {passive:true});
  darkScheme.addEventListener('change',refresh);
  for (const preference of [reducedMotion, reducedTransparency, forcedColors]) preference.addEventListener('change',updateAnimation);
  document.addEventListener('visibilitychange',updateAnimation);
  attach(); updateAnimation();
})();
