import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

// Everything with a name is a real node. The distant stars and orbit rings are scenery.
const REGION_COLORS = { product: '#79dfc7', foundation: '#a69aef', operations: '#dfb77a', research: '#8eafdf' };
const KIND_COLORS = { file: '#79dfc7', class: '#a69aef', function: '#dfb77a' };
const CAPABILITY_COLORS = ['#82d8c2', '#8bc9d2', '#a5b7dd', '#b8abd4', '#cbbb98'];
const GOLDEN_ANGLE = Math.PI * (3 - Math.sqrt(5));
const scratch = new THREE.Vector3();

function hash(value) {
  let result = 2166136261;
  for (const char of String(value)) result = Math.imul(result ^ char.charCodeAt(0), 16777619);
  return result >>> 0;
}

function random(seed) {
  let state = seed >>> 0;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function colorFor(node) {
  if (node.kind === 'feature' || node.kind === 'product-universe') return REGION_COLORS.product;
  if (node.kind === 'foundation') return '#9187b7';
  if (node.kind === 'capability') return CAPABILITY_COLORS[hash(node.id) % CAPABILITY_COLORS.length];
  if (KIND_COLORS[node.kind]) return KIND_COLORS[node.kind];
  return REGION_COLORS[node.category] || REGION_COLORS.research;
}

function importance(node) {
  const metrics = node.metrics || {};
  return 1 + Math.log2(1 + (metrics.files || 0)) * 2.2
    + Math.log2(1 + (metrics.symbols || 0)) * 0.65
    + Math.log2(1 + (metrics.importedBy || 0)) * 0.7
    + Math.log2(1 + (metrics.lines || 0)) * 0.2;
}

function countLabel(node) {
  const metrics = node.metrics || {};
  if (node.kind === 'product-universe') {
    const count = metrics.features ?? metrics.children ?? 0;
    return `${count.toLocaleString()} ${count === 1 ? 'feature' : 'features'}`;
  }
  if (node.kind === 'feature') {
    const count = metrics.capabilities || metrics.codeAreas || metrics.children || 0;
    const unit = metrics.capabilities ? (count === 1 ? 'capability' : 'capabilities') : (count === 1 ? 'code area' : 'code areas');
    return `${count.toLocaleString()} ${unit}`;
  }
  if (node.kind === 'capability' || node.kind === 'foundation') {
    const count = metrics.codeAreas ?? metrics.areas ?? metrics.children ?? 0;
    return `${count.toLocaleString()} ${count === 1 ? 'code area' : 'code areas'}`;
  }
  if (node.kind === 'directory' || node.kind === 'repository') {
    const count = metrics.files || 0;
    return `${count.toLocaleString()} ${count === 1 ? 'file' : 'files'}`;
  }
  if (node.kind === 'file') {
    const count = metrics.symbols || 0;
    return `${count.toLocaleString()} ${count === 1 ? 'symbol' : 'symbols'}`;
  }
  return node.kind === 'class' ? 'class' : 'function';
}

function createGlowTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 128;
  canvas.height = 128;
  const context = canvas.getContext('2d');
  const gradient = context.createRadialGradient(64, 64, 0, 64, 64, 64);
  gradient.addColorStop(0, 'rgba(255,255,255,1)');
  gradient.addColorStop(0.07, 'rgba(255,255,255,0.95)');
  gradient.addColorStop(0.18, 'rgba(255,255,255,0.40)');
  gradient.addColorStop(0.43, 'rgba(255,255,255,0.09)');
  gradient.addColorStop(1, 'rgba(255,255,255,0)');
  context.fillStyle = gradient;
  context.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(canvas);
}

function createPointTexture() {
  const canvas = document.createElement('canvas');
  canvas.width = 32;
  canvas.height = 32;
  const context = canvas.getContext('2d');
  const gradient = context.createRadialGradient(16, 16, 0, 16, 16, 16);
  gradient.addColorStop(0, '#ffffff');
  gradient.addColorStop(0.22, 'rgba(255,255,255,0.95)');
  gradient.addColorStop(0.55, 'rgba(255,255,255,0.2)');
  gradient.addColorStop(1, 'rgba(255,255,255,0)');
  context.fillStyle = gradient;
  context.fillRect(0, 0, 32, 32);
  return new THREE.CanvasTexture(canvas);
}

function geometryFromPoints(positions, colors) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  if (colors) geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  return geometry;
}

function orbitGeometry(radius, eccentricity = 1, count = 180, start = 0, length = Math.PI * 2) {
  const points = [];
  for (let index = 0; index <= count; index++) {
    const angle = start + index / count * length;
    points.push(new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius * eccentricity, 0));
  }
  return new THREE.BufferGeometry().setFromPoints(points);
}

function releaseGroup(group) {
  group.traverse((object) => {
    if (object.geometry && !object.userData.sharedGeometry) object.geometry.dispose();
    if (object.material) {
      for (const material of Array.isArray(object.material) ? object.material : [object.material]) material.dispose();
    }
  });
  group.clear();
}

// Start in a readable spiral, then let actual relationships pull related nodes closer.
// A stable seed means returning to a scope preserves its geography.
function positionsFor(nodes, edges) {
  const sorted = [...nodes].sort((a, b) => importance(b) - importance(a) || String(a.id).localeCompare(String(b.id)));
  const positions = new Map();
  const count = sorted.length;
  const spread = count <= 8 ? 135 : count <= 35 ? 265 : 300;
  sorted.forEach((node, index) => {
    const rng = random(hash(node.id));
    const angle = index * GOLDEN_ANGLE + (rng() - 0.5) * 0.35;
    const radius = count === 1 ? 0 : Math.sqrt((index + 0.65) / count) * spread;
    positions.set(node.id, new THREE.Vector3(
      Math.cos(angle) * radius * 1.3,
      Math.sin(angle) * radius * 0.83,
      (rng() - 0.5) * Math.min(160, spread * 0.7),
    ));
  });

  if (count < 3 || count > 450) return positions;
  const initial = sorted.map((node) => positions.get(node.id).clone());
  const indexById = new Map(sorted.map((node, index) => [node.id, index]));
  const points = sorted.map((node) => positions.get(node.id));
  const usableEdges = edges.filter((edge) => indexById.has(edge.source) && indexById.has(edge.target) && edge.source !== edge.target)
    .sort((a, b) => (b.weight || 1) - (a.weight || 1)).slice(0, 1800);
  const degrees = new Array(count).fill(1);
  for (const edge of usableEdges) {
    degrees[indexById.get(edge.source)]++;
    degrees[indexById.get(edge.target)]++;
  }
  const forces = sorted.map(() => new THREE.Vector3());
  const separation = count < 20 ? 48 : 29;
  for (let step = 0; step < 52; step++) {
    for (let i = 0; i < count; i++) {
      forces[i].copy(initial[i]).sub(points[i]).multiplyScalar(0.18);
      for (let j = 0; j < i; j++) {
        const dx = points[i].x - points[j].x;
        const dy = points[i].y - points[j].y;
        const distance = Math.sqrt(dx * dx + dy * dy) || 0.1;
        if (distance > separation * 3) continue;
        const strength = Math.min(2.8, separation * separation / (distance * distance)) * 0.7;
        forces[i].x += dx / distance * strength;
        forces[i].y += dy / distance * strength;
        forces[j].x -= dx / distance * strength;
        forces[j].y -= dy / distance * strength;
      }
    }
    for (const edge of usableEdges) {
      const sourceIndex = indexById.get(edge.source);
      const targetIndex = indexById.get(edge.target);
      const from = points[sourceIndex];
      const to = points[targetIndex];
      const distance = Math.max(1, from.distanceTo(to));
      const strength = Math.min(1.25, Math.log2(1 + (edge.weight || 1)) * 0.16) * Math.max(0, distance - 80) / distance;
      const dx = (to.x - from.x) * strength * 0.028;
      const dy = (to.y - from.y) * strength * 0.028;
      forces[sourceIndex].x += dx / degrees[sourceIndex];
      forces[sourceIndex].y += dy / degrees[sourceIndex];
      forces[targetIndex].x -= dx / degrees[targetIndex];
      forces[targetIndex].y -= dy / degrees[targetIndex];
    }
    // Dense hubs may have hundreds of links. Bound a layout step so their summed
    // attraction cannot overshoot and send coordinates outside the GPU's range.
    points.forEach((point, index) => point.add(forces[index].clampLength(0, 3)));
  }
  return positions;
}

// Product worlds have their own geography. Their size and position do not depend
// on file volume, so a shared library cannot crowd out a user-facing feature.
function productPositionsFor(nodes) {
  const positions = new Map();
  const count = nodes.length;
  nodes.forEach((node, index) => {
    if (count === 1) { positions.set(node.id, new THREE.Vector3()); return; }
    const rng = random(hash(`${node.id}:world`));
    const angle = Math.PI * 0.74 - index * Math.PI * 2 / count;
    const radius = (count <= 4 ? 150 : count <= 8 ? 205 : 235) * (0.94 + rng() * 0.12);
    // A second, staggered orbit keeps larger capability collections readable.
    const band = count > 12 && index % 2 ? 0.58 : 1;
    positions.set(node.id, new THREE.Vector3(
      Math.cos(angle) * radius * 1.38 * band,
      Math.sin(angle) * radius * 0.83 * band,
      (rng() - 0.5) * 90,
    ));
  });
  return positions;
}

export class Universe {
  constructor(container, callbacks = {}) {
    this.container = container;
    this.callbacks = callbacks;
    this.disposed = false;
    this.nodes = [];
    this.edges = [];
    this.visuals = new Map();
    this.selectedId = null;
    this.hoveredId = null;
    this.lastActivation = null;
    this.edgesVisible = true;
    this.motion = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.width = 1;
    this.height = 1;
    this.clock = new THREE.Clock();
    this.lastLabelTime = 0;
    this.stage = document.createElement('div');
    this.stage.className = 'atlas-universe-stage';
    Object.assign(this.stage.style, { position: 'absolute', inset: '0', overflow: 'hidden', touchAction: 'none' });
    this.stage.setAttribute('aria-label', 'Interactive three dimensional code map. Drag to orbit, scroll to zoom. Use the directory list to navigate with a keyboard.');
    container.appendChild(this.stage);

    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' });
    } catch {
      this.reportError('Your browser could not start the 3D view. You can still explore from the list on the left.');
      return;
    }
    this.renderer.setClearColor(0x080d12, 0);
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.1;
    this.renderer.domElement.style.display = 'block';
    this.renderer.domElement.setAttribute('aria-hidden', 'true');
    this.stage.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.scene.fog = new THREE.FogExp2(0x080d12, 0.00024);
    this.camera = new THREE.PerspectiveCamera(48, 1, 0.5, 3000);
    this.camera.position.set(0, 25, 700);
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.065;
    this.controls.rotateSpeed = 0.38;
    this.controls.zoomSpeed = 0.72;
    this.controls.panSpeed = 0.65;
    this.controls.minDistance = 45;
    this.controls.maxDistance = 1600;
    this.controls.maxPolarAngle = Math.PI;
    this.controls.addEventListener('start', this.onControlStart = () => { this.flight = null; this.requestFrame(); });
    this.controls.addEventListener('change', this.onControlsChange = () => this.requestFrame());
    this.glowTexture = createGlowTexture();
    this.pointTexture = createPointTexture();
    this.coreGeometry = new THREE.SphereGeometry(1, 12, 8);
    this.planetGeometry = new THREE.SphereGeometry(1, 32, 24);
    this.graph = new THREE.Group();
    this.scenery = new THREE.Group();
    this.scene.add(this.scenery, this.graph);
    const fillLight = new THREE.HemisphereLight('#b6e8de', '#172432', 1.15);
    const sunlight = new THREE.DirectionalLight('#c2ffe9', 2.5);
    sunlight.position.set(-170, 220, 360);
    this.scenery.add(fillLight, sunlight);
    this.buildScenery();

    this.labels = document.createElement('div');
    this.labels.className = 'atlas-universe-labels';
    Object.assign(this.labels.style, { position: 'absolute', inset: '0', pointerEvents: 'none', overflow: 'hidden' });
    this.stage.appendChild(this.labels);
    this.bindEvents();
    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
    this.requestFrame();
    callbacks.onReady?.();
  }

  showFallback(message) {
    const fallback = document.createElement('div');
    Object.assign(fallback.style, {
      position: 'absolute', left: '50%', top: '45%', transform: 'translate(-50%,-50%)',
      maxWidth: '340px', width: 'calc(100% - 64px)', color: '#a8bdc5', font: '14px/1.7 system-ui, sans-serif', textAlign: 'center',
      padding: '28px', border: '1px solid rgba(139,184,190,.16)', borderRadius: '14px', background: '#0c141c',
    });
    fallback.textContent = message;
    fallback.setAttribute('role', 'status');
    this.stage.appendChild(fallback);
  }

  reportError(message) {
    this.error = message;
    if (this.callbacks.onError) this.callbacks.onError(message);
    else this.showFallback(message);
  }

  buildScenery() {
    const rng = random(271828);
    const starPositions = [];
    const starColors = [];
    for (let index = 0; index < 1700; index++) {
      starPositions.push((rng() - 0.5) * 2200, (rng() - 0.5) * 1800, -250 - rng() * 1100);
      const brightness = 0.13 + rng() * 0.46;
      starColors.push(brightness * 0.74, brightness * 0.88, brightness);
    }
    this.stars = new THREE.Points(geometryFromPoints(starPositions, starColors), new THREE.PointsMaterial({
      size: 1.7, map: this.pointTexture, transparent: true, opacity: 0.78,
      vertexColors: true, depthWrite: false, blending: THREE.AdditiveBlending, sizeAttenuation: true,
    }));
    this.scenery.add(this.stars);

    const nebulaDefinitions = [
      { position: [-185, 90, -180], color: '#1d5e55', width: 730, height: 570, opacity: 0.13 },
      { position: [220, -110, -240], color: '#3a315d', width: 650, height: 560, opacity: 0.12 },
      { position: [30, -160, -160], color: '#19505c', width: 500, height: 360, opacity: 0.1 },
    ];
    for (const definition of nebulaDefinitions) {
      const cloud = new THREE.Sprite(new THREE.SpriteMaterial({
        map: this.glowTexture, color: definition.color, opacity: definition.opacity,
        transparent: true, blending: THREE.AdditiveBlending, depthWrite: false,
      }));
      cloud.position.set(...definition.position);
      cloud.scale.set(definition.width, definition.height, 1);
      this.scenery.add(cloud);
    }
    this.orbits = new THREE.Group();
    for (let index = 0; index < 5; index++) {
      const line = new THREE.Line(orbitGeometry(110 + index * 66, 0.84, 220), new THREE.LineBasicMaterial({
        color: index % 2 ? '#35535b' : '#304751', transparent: true, opacity: index === 0 ? 0.19 : 0.12, depthWrite: false,
      }));
      line.rotation.x = 0.45 + index * 0.035;
      line.rotation.y = -0.27;
      line.rotation.z = 0.21;
      line.position.z = -100 - index * 7;
      this.orbits.add(line);
    }
    // Sparse ticks make the orbits feel like a navigable atlas without inventing labels.
    const tickPositions = [];
    for (let index = 0; index < 72; index++) {
      const angle = index / 72 * Math.PI * 2;
      for (const radius of [373, index % 6 ? 376 : 380]) tickPositions.push(Math.cos(angle) * radius, Math.sin(angle) * radius * 0.84, -130);
    }
    const ticks = new THREE.LineSegments(geometryFromPoints(tickPositions), new THREE.LineBasicMaterial({
      color: '#536872', transparent: true, opacity: 0.15, depthWrite: false,
    }));
    ticks.rotation.set(0.55, -0.27, 0.21);
    this.orbits.add(ticks);
    this.scenery.add(this.orbits);
  }

  bindEvents() {
    const canvas = this.renderer.domElement;
    this.onPointerDown = (event) => {
      this.pointerStart = { x: event.clientX, y: event.clientY, time: performance.now(), id: event.pointerId };
      this.pointerDragging = false;
    };
    this.onPointerMove = (event) => {
      if (this.pointerStart && Math.hypot(event.clientX - this.pointerStart.x, event.clientY - this.pointerStart.y) > 5) this.pointerDragging = true;
      if (this.pointerDragging) return;
      const visual = this.hitTest(event.clientX, event.clientY);
      this.setHovered(visual?.node.id || null);
      canvas.style.cursor = visual ? 'pointer' : 'grab';
    };
    this.onPointerUp = (event) => {
      if (this.pointerStart?.id !== event.pointerId) return;
      const moved = Math.hypot(event.clientX - this.pointerStart.x, event.clientY - this.pointerStart.y);
      if (moved < 6 && !this.pointerDragging) {
        const visual = this.hitTest(event.clientX, event.clientY);
        if (visual) this.activateNode(visual.node, event);
      }
      this.pointerStart = null;
      this.pointerDragging = false;
    };
    this.onPointerLeave = () => { this.setHovered(null); };
    this.onPointerCancel = () => { this.pointerStart = null; this.pointerDragging = false; };
    this.onContextLost = (event) => {
      event.preventDefault();
      this.contextLost = true;
      this.labels.style.display = 'none';
      this.reportError('The 3D view paused. Reload this page to restore it, or keep exploring from the list on the left.');
    };
    this.onVisibilityChange = () => { if (!document.hidden) this.requestFrame(); };
    canvas.addEventListener('pointerdown', this.onPointerDown);
    canvas.addEventListener('pointermove', this.onPointerMove);
    window.addEventListener('pointerup', this.onPointerUp);
    canvas.addEventListener('pointerleave', this.onPointerLeave);
    canvas.addEventListener('pointercancel', this.onPointerCancel);
    canvas.addEventListener('webglcontextlost', this.onContextLost);
    document.addEventListener('visibilitychange', this.onVisibilityChange);
  }

  setData(data = {}, selectedId = null) {
    this.nodes = data.nodes || [];
    this.edges = data.edges || [];
    this.parent = data.parent || null;
    this.productMode = data.layer === 'product' || ['product-universe', 'feature', 'capability'].includes(this.parent?.kind);
    this.stage.setAttribute('aria-label', `Interactive three dimensional ${this.productMode ? 'product' : 'code'} map. Drag to orbit, scroll to zoom. Use the list to navigate with a keyboard.`);
    this.selectedId = selectedId;
    this.hoveredId = null;
    // A pointer gesture begun in the previous scope must not select a new node.
    this.pointerStart = null;
    this.pointerDragging = false;
    this.callbacks.onHover?.(null);
    if (!this.renderer) return;
    releaseGroup(this.graph);
    this.labels.replaceChildren();
    this.visuals.clear();
    this.positions = this.productMode ? productPositionsFor(this.nodes) : positionsFor(this.nodes, this.edges);
    this.sortedNodes = this.productMode ? [...this.nodes] : [...this.nodes].sort((a, b) => importance(b) - importance(a) || String(a.id).localeCompare(String(b.id)));
    this.orbits.visible = !this.productMode;
    this.maxImportance = Math.max(1, ...this.sortedNodes.map(importance));
    this.graphBuildTime = performance.now();
    const dustPositions = [];
    const dustColors = [];

    this.sortedNodes.forEach((node, rank) => {
      const position = this.positions.get(node.id);
      const weight = this.productMode ? 0.8 : importance(node) / this.maxImportance;
      const color = new THREE.Color(colorFor(node));
      const size = this.productMode ? 8.5 + Math.min(2, Math.sqrt(node.metrics?.children || 0) * 0.45) : 1.5 + Math.pow(weight, 2) * 4.2;
      const nodeGroup = new THREE.Group();
      nodeGroup.position.copy(position);

      const glow = new THREE.Sprite(new THREE.SpriteMaterial({
        map: this.glowTexture, color, transparent: true, opacity: this.productMode ? 0.33 : 0.42 + weight * 0.22,
        depthWrite: false, blending: THREE.AdditiveBlending,
      }));
      glow.scale.setScalar(size * (this.productMode ? 9 : 12));
      const body = new THREE.Mesh(this.productMode ? this.planetGeometry : this.coreGeometry,
        this.productMode
          ? new THREE.MeshStandardMaterial({ color, roughness: 0.7, metalness: 0.07, emissive: color, emissiveIntensity: 0.12 })
          : new THREE.MeshBasicMaterial({ color: color.clone().lerp(new THREE.Color('#ffffff'), 0.2) }));
      body.scale.setScalar(size * (this.productMode ? 0.88 : 0.54));
      body.userData.sharedGeometry = true;
      nodeGroup.add(glow, body);

      let localOrbit = null;
      if (this.productMode || rank < 18 || node.id === selectedId) {
        localOrbit = new THREE.Line(orbitGeometry(this.productMode ? size * 1.8 + 6 : size * 3.7 + 4, 0.66, 80, 0.18, Math.PI * 1.72), new THREE.LineBasicMaterial({
          color, transparent: true, opacity: this.productMode ? 0.2 : rank < 7 ? 0.22 : 0.11, depthWrite: false,
        }));
        localOrbit.rotation.set(0.1 + (hash(node.id) % 5) * 0.1, 0.25, (hash(node.id) % 10) * 0.3);
        nodeGroup.add(localOrbit);
      }
      this.graph.add(nodeGroup);

      // Flecks sample actual contained items; they are never extra graph nodes.
      const containedCount = this.productMode
        ? (node.metrics?.capabilities || node.metrics?.codeAreas || node.metrics?.areas || node.metrics?.children || 0)
        : (node.kind === 'directory' || node.kind === 'repository') ? (node.metrics?.files || 0) : 0;
      const fleckCount = Math.min(containedCount, this.productMode ? 24 : 90);
      const rng = random(hash(node.id + ':files'));
      for (let index = 0; index < fleckCount; index++) {
        const angle = this.productMode ? index / fleckCount * Math.PI * 2 + 0.15 : rng() * Math.PI * 2;
        const radius = this.productMode ? size * 1.8 + 6 : size * 3.5 + Math.pow(rng(), 0.7) * (9 + weight * 26);
        if (this.productMode) {
          const moon = new THREE.Vector3(Math.cos(angle) * radius, Math.sin(angle) * radius * 0.66, 0);
          if (localOrbit) moon.applyQuaternion(localOrbit.quaternion);
          moon.add(position);
          dustPositions.push(moon.x, moon.y, moon.z);
        } else dustPositions.push(position.x + Math.cos(angle) * radius, position.y + Math.sin(angle) * radius * 0.63, position.z + (rng() - 0.5) * 14);
        const brightness = 0.25 + rng() * 0.6;
        dustColors.push(color.r * brightness, color.g * brightness, color.b * brightness);
      }

      const label = document.createElement('button');
      label.type = 'button';
      label.className = 'atlas-star-label';
      label.dataset.nodeId = node.id;
      label.setAttribute('aria-label', `${node.name}, ${countLabel(node)}. ${node.metrics?.children ? 'Open this part of the map.' : 'Read details.'}`);
      Object.assign(label.style, {
        position: 'absolute', left: '0', top: '0', display: 'none', width: 'max-content', maxWidth: this.productMode ? '240px' : '190px',
        padding: '5px 8px', border: '1px solid transparent', borderRadius: '6px', background: 'transparent',
        color: '#a9bdc3', textAlign: this.productMode ? 'center' : 'left', pointerEvents: 'auto', cursor: 'pointer',
        font: `${this.productMode ? '500 14px' : rank < 8 ? '500 13px' : '400 11px'}/1.25 "DM Sans", "Inter", system-ui, sans-serif`,
        letterSpacing: rank < 8 ? '0.005em' : '0.015em', textShadow: '0 1px 8px #080d12, 0 0 15px #080d12',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        transition: 'color 140ms, background 140ms, border-color 140ms',
      });
      const name = document.createElement('span');
      name.textContent = node.name;
      name.style.display = 'block';
      name.style.overflow = 'hidden';
      name.style.textOverflow = 'ellipsis';
      label.appendChild(name);
      const count = document.createElement('span');
      count.textContent = countLabel(node);
      Object.assign(count.style, { display: this.productMode || rank < 9 ? 'block' : 'none', font: `400 ${this.productMode ? 10 : 9}px/1.6 "DM Sans", "Inter", system-ui, sans-serif`, letterSpacing: '0.035em', color: '#657e86', marginTop: '2px' });
      label.appendChild(count);
      label.addEventListener('pointerdown', (event) => event.stopPropagation());
      label.addEventListener('click', (event) => {
        event.stopPropagation();
        this.activateNode(node, event);
      });
      label.addEventListener('pointerenter', () => this.setHovered(node.id));
      label.addEventListener('pointerleave', () => this.setHovered(null));
      label.addEventListener('focus', () => this.setHovered(node.id));
      label.addEventListener('blur', () => this.setHovered(null));
      this.labels.appendChild(label);
      this.visuals.set(node.id, { node, group: nodeGroup, body, glow, localOrbit, label, count, color, rank, size, weight, screen: new THREE.Vector3(), labelWidth: Math.min(this.productMode ? 240 : 190, Math.max(this.productMode ? 105 : 65, node.name.length * (this.productMode ? 8 : rank < 8 ? 7.6 : 6.3) + 18)) });
    });

    if (dustPositions.length) {
      this.dust = new THREE.Points(geometryFromPoints(dustPositions, dustColors), new THREE.PointsMaterial({
        size: this.productMode ? 2.8 : 1.55, map: this.pointTexture, transparent: true, opacity: 0.9,
        vertexColors: true, depthWrite: false, blending: THREE.AdditiveBlending,
      }));
      this.graph.add(this.dust);
    }
    this.buildEdges();
    this.selectionRing = new THREE.Line(orbitGeometry(16, 1, 96), new THREE.LineBasicMaterial({
      color: '#9af4db', transparent: true, opacity: 0.7, depthWrite: false,
    }));
    this.selectionRing.visible = false;
    this.graph.add(this.selectionRing);
    this.selectionOuterRing = new THREE.Line(orbitGeometry(22, 1, 96, 0.2, Math.PI * 1.5), new THREE.LineBasicMaterial({
      color: '#9af4db', transparent: true, opacity: 0.25, depthWrite: false,
    }));
    this.selectionOuterRing.visible = false;
    this.graph.add(this.selectionOuterRing);
    this.select(selectedId);
    this.reset(true);
    this.lastLabelTime = 0;
    this.requestFrame();
  }

  curveFor(edge) {
    const from = this.positions.get(edge.source);
    const to = this.positions.get(edge.target);
    const middle = from.clone().add(to).multiplyScalar(0.5);
    const distance = from.distanceTo(to);
    const bend = ((hash(`${edge.source}:${edge.target}`) % 100) / 100 - 0.5);
    middle.z += 18 + distance * 0.17;
    middle.x += (to.y - from.y) * bend * 0.13;
    middle.y -= (to.x - from.x) * bend * 0.13;
    return new THREE.QuadraticBezierCurve3(from, middle, to);
  }

  buildEdges() {
    const valid = this.edges.filter((edge) => this.positions.has(edge.source) && this.positions.has(edge.target) && edge.source !== edge.target);
    // Keep the strongest real relationship per directed pair. Drawing overlapping
    // imports/calls as separate arcs obscures both rather than explaining either.
    const pairs = new Set();
    const ranked = [...valid].sort((a, b) => (b.weight || 1) - (a.weight || 1)).filter(edge => {
      const pair = `${edge.source}\0${edge.target}`;
      if (pairs.has(pair)) return false;
      pairs.add(pair);
      return true;
    });
    const cap = this.productMode ? Math.min(60, ranked.length) : Math.min(160, this.nodes.length * 3);
    const selected = new Set();
    const covered = new Set();
    // Give less central nodes a real link before filling the remaining budget.
    // Every node remains visible, including nodes with no relationships at all.
    for (const node of this.sortedNodes) {
      if (covered.has(node.id) || selected.size >= cap) continue;
      const edge = ranked.find(candidate => candidate.source === node.id || candidate.target === node.id);
      if (edge) { selected.add(edge); covered.add(edge.source); covered.add(edge.target); }
    }
    for (const edge of ranked) {
      if (selected.size >= cap) break;
      selected.add(edge);
    }
    this.drawnEdges = [...selected];
    const points = [];
    const colors = [];
    for (const edge of this.drawnEdges) {
      const samples = this.curveFor(edge).getPoints(18);
      const color = new THREE.Color('#365c65');
      const strength = Math.min(1, 0.4 + Math.log2(1 + (edge.weight || 1)) / 9);
      color.multiplyScalar(strength);
      for (let index = 1; index < samples.length; index++) {
        points.push(...samples[index - 1].toArray(), ...samples[index].toArray());
        colors.push(color.r, color.g, color.b, color.r, color.g, color.b);
      }
    }
    this.edgeLines = new THREE.LineSegments(geometryFromPoints(points, colors), new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: this.productMode ? 0.12 : this.nodes.length > 100 ? 0.07 : 0.1,
      depthWrite: false,
    }));
    this.edgeLines.visible = this.edgesVisible;
    this.graph.add(this.edgeLines);
    this.activeEdges = new THREE.Group();
    this.activeEdges.visible = this.edgesVisible;
    this.graph.add(this.activeEdges);
  }

  updateActiveEdges() {
    if (!this.activeEdges) return;
    releaseGroup(this.activeEdges);
    const id = this.hoveredId || this.selectedId;
    const pairs = new Set();
    const active = this.edges.filter((edge) => (edge.source === id || edge.target === id) && this.positions.has(edge.source) && this.positions.has(edge.target) && edge.source !== edge.target)
      .sort((a, b) => (b.weight || 1) - (a.weight || 1)).filter(edge => {
        const pair = `${edge.source}\0${edge.target}`;
        if (pairs.has(pair)) return false;
        pairs.add(pair);
        return true;
      }).slice(0, this.productMode ? 24 : this.hoveredId ? 15 : 12);
    this.activeDrawnEdges = active;
    this.connectedIds = new Set();
    for (const edge of active) {
      this.connectedIds.add(edge.source);
      this.connectedIds.add(edge.target);
    }
    const positions = [];
    const colors = [];
    const incomingColor = new THREE.Color('#afa1d9');
    const outgoingColor = new THREE.Color('#6ac8b7');
    for (const edge of active) {
      const curve = this.curveFor(edge);
      const samples = curve.getPoints(28);
      const color = edge.source === id ? outgoingColor : incomingColor;
      for (let index = 1; index < samples.length; index++) {
        positions.push(...samples[index - 1].toArray(), ...samples[index].toArray());
        const brightness = 0.3 + Math.sin(index / samples.length * Math.PI) * 0.45;
        colors.push(color.r * brightness, color.g * brightness, color.b * brightness, color.r * brightness, color.g * brightness, color.b * brightness);
      }
      // A small chevron on the actual curve shows source → target.
      const tip = curve.getPoint(0.76);
      const tangent = curve.getTangent(0.76).normalize();
      const side = new THREE.Vector3(-tangent.y, tangent.x, 0).normalize();
      const left = tip.clone().addScaledVector(tangent, -4).addScaledVector(side, 1.7);
      const right = tip.clone().addScaledVector(tangent, -4).addScaledVector(side, -1.7);
      positions.push(...left.toArray(), ...tip.toArray(), ...tip.toArray(), ...right.toArray());
      for (let index = 0; index < 4; index++) colors.push(color.r * 0.6, color.g * 0.6, color.b * 0.6);
    }
    const lines = new THREE.LineSegments(geometryFromPoints(positions, colors), new THREE.LineBasicMaterial({
      vertexColors: true, transparent: true, opacity: this.productMode ? 0.36 : this.hoveredId ? 0.25 : 0.2,
      depthWrite: false,
    }));
    this.activeEdges.add(lines);
  }

  activateNode(node, event) {
    if (event.detail > 1) return;
    const pointerActivation = event.type === 'pointerup' || event.detail > 0;
    if (pointerActivation) {
      const now = performance.now();
      const last = this.lastActivation;
      // Keep this record across scope changes: the second tap must not land on
      // an unrelated node that has just appeared under the same pointer.
      if (last && now - last.time < 350 && Math.hypot(event.clientX - last.x, event.clientY - last.y) <= 8) return;
      this.lastActivation = { time: now, x: event.clientX, y: event.clientY };
    }
    this.select(node.id);
    this.callbacks.onSelect?.(node);
  }

  select(id) {
    this.selectedId = this.visuals.has(id) ? id : null;
    if (!this.renderer) return;
    const visual = this.visuals.get(this.selectedId);
    if (this.selectionRing) {
      this.selectionRing.visible = Boolean(visual);
      this.selectionOuterRing.visible = Boolean(visual);
      if (visual) {
        this.selectionRing.position.copy(visual.group.position);
        this.selectionOuterRing.position.copy(visual.group.position);
        const scale = Math.max(0.65, visual.size / 4);
        this.selectionRing.scale.setScalar(scale);
        this.selectionOuterRing.scale.setScalar(scale);
        this.selectionRing.material.color.copy(visual.color);
        this.selectionOuterRing.material.color.copy(visual.color);
      }
    }
    this.updateActiveEdges();
    this.updateLabelStyles();
    this.lastLabelTime = 0;
    this.requestFrame();
  }

  setHovered(id) {
    if (id === this.hoveredId) return;
    this.hoveredId = id;
    this.callbacks.onHover?.(this.visuals.get(id)?.node || null);
    this.updateActiveEdges();
    this.updateLabelStyles();
    this.lastLabelTime = 0;
    this.requestFrame();
  }

  updateLabelStyles() {
    for (const [id, visual] of this.visuals) {
      const selected = id === this.selectedId;
      const hovered = id === this.hoveredId;
      visual.label.style.color = selected || hovered ? '#e3f7f1' : this.productMode || visual.rank < 9 ? '#b7cdce' : '#829da6';
      visual.label.style.background = selected ? 'rgba(17,38,37,.88)' : hovered ? 'rgba(13,26,32,.91)' : 'transparent';
      visual.label.style.borderColor = selected ? 'rgba(111,233,203,.32)' : hovered ? 'rgba(111,233,203,.16)' : 'transparent';
      visual.label.style.zIndex = selected || hovered ? '4' : '1';
      visual.label.setAttribute('aria-pressed', String(selected));
      visual.count.style.display = this.productMode || selected || hovered || visual.rank < 9 ? 'block' : 'none';
      visual.count.style.color = selected ? '#7ba79c' : '#657e86';
    }
  }

  hitTest(clientX, clientY) {
    const bounds = this.stage.getBoundingClientRect();
    const x = clientX - bounds.left;
    const y = clientY - bounds.top;
    let best = null;
    let bestDistance = Infinity;
    for (const visual of this.visuals.values()) {
      const screen = visual.screen;
      if (screen.z > 1 || screen.z < -1) continue;
      const distance = Math.hypot(x - screen.x, y - screen.y);
      if (distance < Math.max(12, visual.projectedSize || 12) && distance < bestDistance) { best = visual; bestDistance = distance; }
    }
    return best;
  }

  focus(id) {
    const visual = this.visuals.get(id);
    if (!visual || !this.camera) return;
    this.select(id);
    const target = visual.group.getWorldPosition(new THREE.Vector3());
    const direction = this.camera.position.clone().sub(this.controls.target).normalize();
    const distance = Math.max(140, Math.min(270, 170 + visual.size * 14));
    this.flyTo(target, target.clone().addScaledVector(direction, distance));
  }

  reset(immediate = false) {
    if (!this.camera) return;
    const box = new THREE.Box3();
    for (const position of this.positions?.values() || []) box.expandByPoint(position);
    const size = box.isEmpty() ? new THREE.Vector3(500, 500, 100) : box.getSize(new THREE.Vector3());
    const center = box.isEmpty() ? new THREE.Vector3() : box.getCenter(new THREE.Vector3());
    center.z = 0;
    const fov = THREE.MathUtils.degToRad(this.camera.fov);
    const bottomReserve = this.productMode ? Math.min(105, this.height * 0.24) : 0;
    const topReserve = this.productMode ? 12 : 0;
    const usableHeight = Math.max(120, this.height - bottomReserve - topReserve);
    const verticalFit = (Math.max(170, size.y) + 85) * this.height / usableHeight / (2 * Math.tan(fov / 2));
    const horizontalFit = (Math.max(220, size.x) + 120) / (2 * Math.tan(fov / 2) * Math.max(0.4, this.camera.aspect));
    const distance = Math.max(300, Math.max(verticalFit, horizontalFit) + size.z * 0.35);
    this.defaultDistance = distance;
    if (this.productMode) {
      const worldUnitsPerPixel = 2 * distance * Math.tan(fov / 2) / this.height;
      // Aim below the layout's center, leaving its lower labels above the toolbar.
      center.y -= (bottomReserve - topReserve) / 2 * worldUnitsPerPixel;
    }
    const position = center.clone().add(new THREE.Vector3(0, 20, distance));
    if (immediate || !this.motion) {
      this.flight = null;
      this.controls.target.copy(center);
      this.camera.position.copy(position);
      this.controls.update();
    } else this.flyTo(center, position);
  }

  flyTo(target, position) {
    if (!this.motion) {
      this.controls.target.copy(target);
      this.camera.position.copy(position);
      this.controls.update();
      return;
    }
    this.flight = {
      start: performance.now(), duration: 850,
      fromPosition: this.camera.position.clone(), fromTarget: this.controls.target.clone(),
      toPosition: position, toTarget: target,
    };
    this.requestFrame();
  }

  zoom(amount) {
    if (!this.camera) return;
    const offset = this.camera.position.clone().sub(this.controls.target);
    const distance = THREE.MathUtils.clamp(offset.length() * Math.exp(-amount * 0.21), this.controls.minDistance, this.controls.maxDistance);
    const position = this.controls.target.clone().add(offset.normalize().multiplyScalar(distance));
    this.flyTo(this.controls.target.clone(), position);
  }

  setEdgesVisible(visible) {
    this.edgesVisible = Boolean(visible);
    if (this.edgeLines) this.edgeLines.visible = this.edgesVisible;
    if (this.activeEdges) this.activeEdges.visible = this.edgesVisible;
    this.requestFrame();
  }

  setMotion(motion) { this.motion = Boolean(motion); this.requestFrame(); }

  setVisible(visible) {
    this.visible = Boolean(visible);
    if (!this.visible) {
      cancelAnimationFrame(this.animationFrame);
      this.animationFrame = null;
    } else this.requestFrame();
  }

  get stats() {
    const shown = new Set([...(this.drawnEdges || []), ...(this.activeDrawnEdges || [])].map(edge => `${edge.source}\0${edge.target}\0${edge.kind}`));
    return {
      nodes: this.nodes.length, edges: this.edgesVisible ? shown.size : 0, totalEdges: this.edges.length,
      baseEdges: this.drawnEdges?.length || 0, labels: this.visibleLabelCount || 0,
    };
  }

  resize() {
    if (!this.renderer || this.disposed) return;
    const bounds = this.container.getBoundingClientRect();
    const oldAspect = this.camera.aspect;
    const oldHeight = this.height;
    this.width = Math.max(1, bounds.width);
    this.height = Math.max(1, bounds.height);
    this.camera.aspect = this.width / this.height;
    this.camera.updateProjectionMatrix();
    this.renderer.setSize(this.width, this.height);
    if ((Math.abs(oldAspect - this.camera.aspect) > 0.25 || (this.productMode && Math.abs(oldHeight - this.height) > 16)) && this.nodes.length && !this.pointerDragging) this.reset(true);
    this.lastLabelTime = 0;
    this.requestFrame();
  }

  updateLabels() {
    const projected = [];
    this.camera.updateMatrixWorld();
    for (const visual of this.visuals.values()) {
      visual.group.getWorldPosition(scratch);
      const distance = this.camera.position.distanceTo(scratch);
      scratch.project(this.camera);
      visual.screen.set((scratch.x * 0.5 + 0.5) * this.width, (-scratch.y * 0.5 + 0.5) * this.height, scratch.z);
      visual.projectedSize = visual.size * this.height / (distance * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2)));
      const forced = visual.node.id === this.selectedId || visual.node.id === this.hoveredId;
      const connected = this.connectedIds?.has(visual.node.id);
      const priority = (forced ? 10000 : 0) + (connected ? 30 : 0) + (1 - visual.rank / Math.max(1, this.nodes.length)) * 35 + visual.projectedSize;
      projected.push({ visual, priority, forced });
    }
    projected.sort((a, b) => b.priority - a.priority);
    const occupied = [];
    const maxLabels = this.productMode && this.nodes.length <= 12 ? this.nodes.length : Math.min(55, Math.max(16, Math.floor(this.width * this.height / 11500)));
    const labelBottom = this.height - (this.productMode ? Math.min(90, this.height * 0.22) : 7);
    let displayed = 0;
    for (const { visual, forced } of projected) {
      const { x, y, z } = visual.screen;
      if (z < -1 || z > 1 || x < 14 || x > this.width - 14 || y < 12 || y > this.height - 18 || (!forced && displayed >= maxLabels)) {
        visual.label.style.display = 'none';
        continue;
      }
      const labelHeight = this.productMode || forced || visual.rank < 9 ? 43 : 28;
      const labelWidth = visual.labelWidth;
      const offset = Math.max(this.productMode ? 17 : 8, Math.min(this.productMode ? 33 : 20, visual.projectedSize * 0.65));
      const candidates = [
        { x: x + offset, y: y - labelHeight * 0.42 },
        { x: x - labelWidth - offset, y: y - labelHeight * 0.42 },
        { x: x - labelWidth / 2, y: y + offset },
        { x: x - labelWidth / 2, y: y - labelHeight - offset },
      ].map((candidate, index) => ({ ...candidate, placement: index }));
      if (this.productMode) candidates.sort((a, b) => Number(b.placement === 2) - Number(a.placement === 2));
      // Keep active labels on the same side of their star while they are being read.
      if (forced && visual.labelPlacement !== undefined) candidates.sort((a, b) => Number(b.placement === visual.labelPlacement) - Number(a.placement === visual.labelPlacement));
      let chosen = null;
      for (const candidate of candidates) {
        if (candidate.x < 7 || candidate.x + labelWidth > this.width - 7 || candidate.y < 7 || candidate.y + labelHeight > labelBottom) continue;
        const rect = { x: candidate.x - 4, y: candidate.y - 2, w: labelWidth + 8, h: labelHeight + 4 };
        if (occupied.some((other) => rect.x < other.x + other.w && rect.x + rect.w > other.x && rect.y < other.y + other.h && rect.y + rect.h > other.y)) continue;
        chosen = { ...candidate, rect };
        break;
      }
      if (!chosen && (forced || (this.productMode && this.nodes.length <= 12))) {
        const overlap = candidate => occupied.reduce((sum, other) => sum
          + Math.max(0, Math.min(candidate.x + labelWidth, other.x + other.w) - Math.max(candidate.x, other.x))
          * Math.max(0, Math.min(candidate.y + labelHeight, other.y + other.h) - Math.max(candidate.y, other.y)), 0);
        const candidate = [...candidates].sort((a, b) => overlap(a) - overlap(b))[0];
        candidate.x = THREE.MathUtils.clamp(candidate.x, 7, this.width - labelWidth - 7);
        candidate.y = THREE.MathUtils.clamp(candidate.y, 7, labelBottom - labelHeight);
        chosen = { ...candidate, rect: { x: candidate.x, y: candidate.y, w: labelWidth, h: labelHeight } };
      }
      if (!chosen) { visual.label.style.display = 'none'; continue; }
      occupied.push(chosen.rect);
      visual.labelPlacement = chosen.placement;
      visual.label.style.display = 'block';
      visual.label.style.transform = `translate3d(${Math.round(chosen.x)}px,${Math.round(chosen.y)}px,0)`;
      displayed++;
    }
    this.visibleLabelCount = displayed;
  }

  requestFrame() {
    if (this.disposed || this.animationFrame || !this.renderer || this.contextLost || document.hidden || this.visible === false) return;
    this.animationFrame = requestAnimationFrame(() => this.animate());
  }

  animate() {
    this.animationFrame = null;
    if (this.disposed || this.contextLost || document.hidden || this.visible === false || this.width < 2 || this.height < 2) return;
    const now = performance.now();
    const elapsed = this.clock.getElapsedTime();
    if (this.flight) {
      const progress = Math.min(1, (now - this.flight.start) / this.flight.duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      this.camera.position.lerpVectors(this.flight.fromPosition, this.flight.toPosition, eased);
      this.controls.target.lerpVectors(this.flight.fromTarget, this.flight.toTarget, eased);
      if (progress === 1) this.flight = null;
    }
    const cameraChanged = this.controls.update();
    if (this.motion) {
      this.stars.rotation.z = Math.sin(elapsed * 0.018) * 0.008;
      for (const visual of this.visuals.values()) {
        const selected = visual.node.id === this.selectedId;
        const hovered = visual.node.id === this.hoveredId;
        visual.glow.material.opacity = (this.productMode ? selected || hovered ? 0.52 : 0.33 : selected || hovered ? 0.8 : 0.42 + visual.weight * 0.22) + Math.sin(elapsed * 0.9 + visual.rank * 1.7) * 0.03;
      }
    }
    if (this.selectionRing?.visible) {
      this.selectionRing.quaternion.copy(this.camera.quaternion);
      this.selectionOuterRing.quaternion.copy(this.camera.quaternion);
      if (this.motion) this.selectionOuterRing.rotateZ(elapsed * 0.13);
    }
    if (this.graphBuildTime && this.motion) {
      const progress = Math.min(1, (now - this.graphBuildTime) / 500);
      // A short arrival glow avoids camera jumps on entry without moving real edges.
      this.graph.scale.setScalar(0.975 + 0.025 * (1 - Math.pow(1 - progress, 3)));
      if (progress === 1) this.graphBuildTime = null;
    }
    this.renderer.render(this.scene, this.camera);
    if (now - this.lastLabelTime > 28) {
      this.updateLabels();
      this.lastLabelTime = now;
    }
    if (this.motion || this.flight || cameraChanged) this.requestFrame();
  }

  dispose() {
    this.disposed = true;
    cancelAnimationFrame(this.animationFrame);
    this.resizeObserver?.disconnect();
    if (this.renderer) {
      const canvas = this.renderer.domElement;
      canvas.removeEventListener('pointerdown', this.onPointerDown);
      canvas.removeEventListener('pointermove', this.onPointerMove);
      window.removeEventListener('pointerup', this.onPointerUp);
      canvas.removeEventListener('pointerleave', this.onPointerLeave);
      canvas.removeEventListener('pointercancel', this.onPointerCancel);
      canvas.removeEventListener('webglcontextlost', this.onContextLost);
      document.removeEventListener('visibilitychange', this.onVisibilityChange);
      this.controls.removeEventListener('start', this.onControlStart);
      this.controls.removeEventListener('change', this.onControlsChange);
      this.controls.dispose();
      releaseGroup(this.graph);
      releaseGroup(this.scenery);
      this.glowTexture.dispose();
      this.pointTexture.dispose();
      this.coreGeometry.dispose();
      this.planetGeometry.dispose();
      this.renderer.dispose();
      this.renderer.forceContextLoss();
    }
    this.stage.remove();
    this.visuals.clear();
  }
}

export default Universe;
