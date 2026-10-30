// Studio-lit 3D desktop (monitor, stand, keyboard) built from primitives in plain Three.js.
// Clicks are resolved by raycasting against the computer's meshes; the parent owns the single-trigger guard.
// Loaded lazily so returning visitors never download three.js.
import { useEffect, useRef } from "react";
import * as THREE from "three";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { RoundedBoxGeometry } from "three/examples/jsm/geometries/RoundedBoxGeometry.js";
import { TIMING } from "./introState";

interface Props {
  /** Becomes true once the visitor has entered; starts the activation timeline. */
  activated: boolean;
  onEnter: () => void;
  onActivated: () => void;
  onReady: () => void;
  onError: () => void;
}

const BG = new THREE.Color("#050505");
const BG_LIT = new THREE.Color("#141416");

const clamp01 = (v: number) => Math.min(1, Math.max(0, v));
const easeOutCubic = (t: number) => 1 - (1 - t) ** 3;
const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

function screenTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = 1024;
  c.height = 640;
  const g = c.getContext("2d")!;
  const grad = g.createLinearGradient(0, 0, 1024, 640);
  grad.addColorStop(0, "#1b1c20");
  grad.addColorStop(0.55, "#08080a");
  grad.addColorStop(1, "#000000");
  g.fillStyle = grad;
  g.fillRect(0, 0, 1024, 640);
  // Faint centre bloom, a hint that the screen is alive.
  const r = g.createRadialGradient(512, 320, 0, 512, 320, 300);
  r.addColorStop(0, "rgba(255,255,255,0.10)");
  r.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = r;
  g.fillRect(0, 0, 1024, 640);
  // Diagonal glass reflection.
  g.fillStyle = "rgba(255,255,255,0.035)";
  g.beginPath();
  g.moveTo(560, 0);
  g.lineTo(760, 0);
  g.lineTo(420, 640);
  g.lineTo(220, 640);
  g.closePath();
  g.fill();
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

function haloTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d")!;
  const r = g.createRadialGradient(128, 128, 0, 128, 128, 128);
  r.addColorStop(0, "rgba(255,255,255,1)");
  r.addColorStop(0.35, "rgba(255,255,255,0.35)");
  r.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = r;
  g.fillRect(0, 0, 256, 256);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  return tex;
}

export default function ThreeDComputer({ activated, onEnter, onActivated, onReady, onError }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const activatedRef = useRef(activated);
  const cb = useRef({ onEnter, onActivated, onReady, onError });
  useEffect(() => {
    activatedRef.current = activated;
    cb.current = { onEnter, onActivated, onReady, onError };
  });

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    let renderer: THREE.WebGLRenderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    } catch {
      cb.current.onError();
      return;
    }
    const canvas = renderer.domElement;
    canvas.style.display = "block";
    canvas.style.width = "100%";
    canvas.style.height = "100%";
    canvas.style.touchAction = "manipulation";
    host.appendChild(canvas);

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 0;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFShadowMap;

    const scene = new THREE.Scene();
    scene.background = BG.clone();
    scene.fog = new THREE.Fog(BG.clone(), 9, 20);
    const pmrem = new THREE.PMREMGenerator(renderer);
    const room = new RoomEnvironment();
    const env = pmrem.fromScene(room, 0.04).texture;
    scene.environment = env;
    scene.environmentIntensity = 0.45;

    const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 60);

    // Materials: satin-black shell, brushed silver aluminium, dark glass.
    const shell = new THREE.MeshPhysicalMaterial({ color: 0x0a0a0b, metalness: 0.85, roughness: 0.3, clearcoat: 0.9, clearcoatRoughness: 0.22 });
    const silver = new THREE.MeshPhysicalMaterial({ color: 0xc8cacf, metalness: 1, roughness: 0.24 });
    const keyMat = new THREE.MeshStandardMaterial({ color: 0x141416, metalness: 0.4, roughness: 0.5 });
    const scrTex = screenTexture();
    const glass = new THREE.MeshPhysicalMaterial({ color: 0x050506, roughness: 0.08, metalness: 0.1, clearcoat: 1,
      emissive: 0xffffff, emissiveMap: scrTex, emissiveIntensity: 0.9 });
    const glowMat = new THREE.MeshBasicMaterial({ color: 0xf6f6f3, transparent: true, opacity: 0, toneMapped: false });
    const halo = haloTexture();
    const haloMat = new THREE.SpriteMaterial({ map: halo, color: 0xffffff, transparent: true, opacity: 0, depthWrite: false,
      blending: THREE.AdditiveBlending, toneMapped: false });
    const floorMat = new THREE.MeshStandardMaterial({ color: 0x050506, roughness: 0.92, metalness: 0.05 });

    const shadowed = (m: THREE.Mesh) => { m.castShadow = true; m.receiveShadow = true; return m; };

    const rig = new THREE.Group(); // the whole desktop; turns and moves toward the viewer on activation
    const monitor = new THREE.Group();
    monitor.position.set(0, 1.62, 0);
    rig.add(monitor);
    monitor.add(shadowed(new THREE.Mesh(new RoundedBoxGeometry(3.4, 2.1, 0.14, 4, 0.06), shell)));
    const rim = shadowed(new THREE.Mesh(new RoundedBoxGeometry(3.45, 2.15, 0.1, 4, 0.05), silver));
    rim.position.z = -0.035;
    monitor.add(rim);
    const back = shadowed(new THREE.Mesh(new RoundedBoxGeometry(2.1, 1.3, 0.22, 4, 0.08), shell));
    back.position.z = -0.15;
    monitor.add(back);
    const screen = new THREE.Mesh(new THREE.PlaneGeometry(3.26, 1.96), glass);
    screen.position.z = 0.071;
    monitor.add(screen);
    const glow = new THREE.Mesh(new THREE.PlaneGeometry(3.26, 1.96), glowMat);
    glow.position.z = 0.073;
    monitor.add(glow);
    const haloSprite = new THREE.Sprite(haloMat);
    haloSprite.position.z = 0.3;
    haloSprite.scale.set(6, 4.4, 1);
    haloSprite.raycast = () => {}; // the halo quad is far larger than the monitor; it must not catch clicks
    monitor.add(haloSprite);
    const screenLight = new THREE.PointLight(0xffffff, 0, 7, 2);
    screenLight.position.set(0, 0, 0.9);
    monitor.add(screenLight);

    const neck = shadowed(new THREE.Mesh(new RoundedBoxGeometry(0.46, 1.35, 0.08, 4, 0.035), silver));
    neck.position.set(0, 0.72, -0.3);
    neck.rotation.x = -0.14;
    rig.add(neck);
    const base = shadowed(new THREE.Mesh(new RoundedBoxGeometry(1.5, 0.05, 0.9, 4, 0.024), silver));
    base.position.set(0, 0.026, -0.18);
    rig.add(base);

    const keyboard = new THREE.Group();
    keyboard.position.set(0, 0, 1.35);
    rig.add(keyboard);
    keyboard.add(shadowed(new THREE.Mesh(new RoundedBoxGeometry(2.5, 0.06, 0.8, 4, 0.028), silver)).translateY(0.03));
    const cols = 14, rows = 5;
    const keys = new THREE.InstancedMesh(new RoundedBoxGeometry(0.135, 0.025, 0.11, 2, 0.012), keyMat, cols * rows);
    const m4 = new THREE.Matrix4();
    for (let r = 0, i = 0; r < rows; r++)
      for (let c = 0; c < cols; c++, i++) keys.setMatrixAt(i, m4.makeTranslation((c - (cols - 1) / 2) * 0.162, 0.066, (r - (rows - 1) / 2) * 0.135));
    keys.castShadow = true;
    keyboard.add(keys);

    // Generous invisible hit volume so the computer is an easy tap target on phones (still a real raycast).
    const hitMat = new THREE.MeshBasicMaterial();
    const hit = new THREE.Mesh(new THREE.BoxGeometry(3.7, 2.9, 2.4), hitMat);
    hit.position.set(0, 1.4, 0.55);
    hit.visible = false;
    rig.add(hit);
    scene.add(rig);

    const floor = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), floorMat);
    floor.rotation.x = -Math.PI / 2;
    floor.receiveShadow = true;
    scene.add(floor);

    // Studio lighting: soft key spot with shadows, cool rims for the silver edges, a faint fill.
    scene.add(new THREE.AmbientLight(0xffffff, 0.06));
    const key = new THREE.SpotLight(0xffffff, 140, 30, 0.42, 0.9, 1.6);
    key.position.set(3.5, 7.5, 5.5);
    key.target.position.set(0, 1, 0);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.bias = -0.0004;
    key.shadow.radius = 6;
    scene.add(key, key.target);
    const rimL = new THREE.DirectionalLight(0xdfe7ff, 2.2);
    rimL.position.set(-6, 4, -5);
    const rimR = new THREE.DirectionalLight(0xffffff, 1.4);
    rimR.position.set(6, 2.5, -4);
    const fill = new THREE.DirectionalLight(0xffffff, 0.25);
    fill.position.set(-4, 2, 6);
    scene.add(rimL, rimR, fill);

    // Camera framing that keeps the desktop inside the viewport at any aspect ratio.
    const lookAt = new THREE.Vector3(0, 0.95, 0.4);
    let baseDist = 10;
    const resize = () => {
      const w = host.clientWidth || 1, h = host.clientHeight || 1;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.fov = camera.aspect < 1 ? 44 : 30; // phones: wider lens so the desktop stays large and close
      const vHalf = THREE.MathUtils.degToRad(camera.fov / 2);
      const hHalf = Math.atan(Math.tan(vHalf) * camera.aspect);
      const fitW = (camera.aspect < 1 ? 2.45 : 2.7) / Math.tan(hHalf); // half-width of the desktop plus margin
      const fitH = 2.75 / Math.tan(vHalf); // room below for the hint
      baseDist = Math.max(fitW, fitH, 7.5);
      const fog = scene.fog as THREE.Fog;
      fog.near = baseDist - 1;
      fog.far = baseDist + 11;
      camera.updateProjectionMatrix();
    };
    resize();
    const ro = typeof ResizeObserver !== "undefined" ? new ResizeObserver(resize) : null;
    ro?.observe(host);

    // Pointer: tap/click raycast, hover affordance, slight parallax.
    const raycaster = new THREE.Raycaster();
    const ndc = new THREE.Vector2();
    const parallax = new THREE.Vector2();
    let hover = 0, hovering = false;
    let down: { x: number; y: number } | null = null;
    const hitTest = (e: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      ndc.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
      raycaster.setFromCamera(ndc, camera);
      return raycaster.intersectObject(rig, true).length > 0;
    };
    const onMove = (e: PointerEvent) => {
      if (activatedRef.current) return;
      hovering = hitTest(e);
      canvas.style.cursor = hovering ? "pointer" : "default";
      if (e.pointerType === "mouse") parallax.set(ndc.x, ndc.y);
    };
    const onDown = (e: PointerEvent) => { down = { x: e.clientX, y: e.clientY }; };
    const onUp = (e: PointerEvent) => {
      const start = down;
      down = null;
      if (!start || activatedRef.current) return;
      if (Math.hypot(e.clientX - start.x, e.clientY - start.y) > 10) return;
      if (hitTest(e)) cb.current.onEnter();
    };
    canvas.addEventListener("pointermove", onMove);
    canvas.addEventListener("pointerdown", onDown);
    canvas.addEventListener("pointerup", onUp);
    const onLost = (e: Event) => { e.preventDefault(); cb.current.onError(); };
    canvas.addEventListener("webglcontextlost", onLost);

    const t0 = performance.now();
    const camPos = new THREE.Vector3();
    const tmpTarget = new THREE.Vector3();
    let readySent = false, doneSent = false;
    let actStart = -1, fromRotY = 0;

    renderer.setAnimationLoop(() => {
      const t = (performance.now() - t0) / 1000;
      // Emerge from darkness: exposure ramps up while the desktop rises slightly.
      const intro = easeOutCubic(clamp01(t / 2.6));
      renderer.toneMappingExposure = 0.95 * intro;
      const idleY = Math.sin(t * 0.6) * 0.035;

      let a = 0;
      if (activatedRef.current) {
        if (actStart < 0) { actStart = t; fromRotY = rig.rotation.y; }
        a = clamp01((t - actStart) / (TIMING.activate3d / 1000));
      }
      const ae = easeInOutCubic(a);

      hover += ((hovering && !activatedRef.current ? 1 : 0) - hover) * 0.12;
      const idleRot = -0.34 + Math.sin(t * 0.35) * 0.06 + parallax.x * 0.06;
      rig.rotation.y = activatedRef.current ? THREE.MathUtils.lerp(fromRotY, 0, ae) : idleRot;
      rig.position.y = (1 - intro) * -0.25 + idleY * (1 - ae);
      rig.position.z = ae * 1.1;

      // Screen glows soft white; the light spills onto the keyboard and stand.
      const glowT = easeOutCubic(clamp01(a / 0.55));
      glowMat.opacity = 0.96 * glowT;
      glass.emissiveIntensity = 0.9 + hover * 0.8;
      screenLight.intensity = 30 * glowT + hover * 2;
      haloMat.opacity = 0.55 * glowT;
      haloSprite.scale.set(6 + glowT * 3, 4.4 + glowT * 2.2, 1);
      key.intensity = 140 * (1 - 0.5 * ae);
      (scene.background as THREE.Color).copy(BG).lerp(BG_LIT, Math.sin(Math.PI * ae) * 0.9 + ae * 0.1);
      scene.fog!.color.copy(scene.background as THREE.Color);

      // Camera: gentle drift while idle, then a dolly in toward the glowing screen.
      const drift = 1 - ae;
      const dist = THREE.MathUtils.lerp(baseDist, Math.max(baseDist * 0.36, 3.2), ae);
      camPos.set(
        Math.sin(t * 0.17) * 0.35 * drift + parallax.x * 0.25 * drift,
        THREE.MathUtils.lerp(2.1, 1.65, ae) + Math.sin(t * 0.23) * 0.08 * drift + parallax.y * 0.12 * drift,
        dist + rig.position.z * 0.2,
      );
      camera.position.copy(camPos);
      tmpTarget.copy(lookAt).lerp(new THREE.Vector3(0, 1.62, 1.1), ae);
      camera.lookAt(tmpTarget);

      renderer.render(scene, camera);
      if (!readySent) { readySent = true; cb.current.onReady(); }
      if (a >= 1 && !doneSent) { doneSent = true; cb.current.onActivated(); }
    });

    return () => {
      renderer.setAnimationLoop(null);
      ro?.disconnect();
      canvas.removeEventListener("pointermove", onMove);
      canvas.removeEventListener("pointerdown", onDown);
      canvas.removeEventListener("pointerup", onUp);
      canvas.removeEventListener("webglcontextlost", onLost);
      scene.traverse((o) => {
        if (o instanceof THREE.Mesh || o instanceof THREE.InstancedMesh) o.geometry.dispose();
      });
      [shell, silver, keyMat, glass, glowMat, haloMat, floorMat, hitMat].forEach((m) => m.dispose());
      [scrTex, halo, env].forEach((x) => x.dispose());
      pmrem.dispose();
      room.dispose();
      renderer.dispose();
      canvas.remove();
    };
  }, []);

  return <div ref={hostRef} className="absolute inset-0" />;
}
