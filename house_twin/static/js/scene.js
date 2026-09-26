/*
 * scene.js — the Three.js model of the building.
 *
 * Geometry comes from /api/house. Readings from /api/readings drive an
 * inverse-distance-weighted temperature field painted onto each floor plate,
 * so the colour between two sensors is interpolated from the readings rather
 * than guessed.
 */

import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";

// ── colour ramp ──────────────────────────────────────────────────────────────
// Cool blue through neutral to warm amber. Anchors are the same olive/amber
// family as the rest of the dashboard so the two apps read as one system.

const RAMP = [
  { t: 0.0, c: [0x2c, 0x5f, 0x8f] },  // cold blue
  { t: 0.25, c: [0x4a, 0x8f, 0x9c] }, // teal
  { t: 0.5, c: [0x8f, 0xa3, 0x6b] },  // neutral olive
  { t: 0.75, c: [0xc4, 0x9a, 0x4a] }, // warm
  { t: 1.0, c: [0xb5, 0x4a, 0x2a] },  // hot amber-red
];

function rampColor(t) {
  const x = Math.max(0, Math.min(1, t));
  for (let i = 1; i < RAMP.length; i++) {
    if (x <= RAMP[i].t) {
      const a = RAMP[i - 1];
      const b = RAMP[i];
      const f = (x - a.t) / (b.t - a.t);
      return [
        Math.round(a.c[0] + (b.c[0] - a.c[0]) * f),
        Math.round(a.c[1] + (b.c[1] - a.c[1]) * f),
        Math.round(a.c[2] + (b.c[2] - a.c[2]) * f),
      ];
    }
  }
  return RAMP[RAMP.length - 1].c;
}

export class HouseScene {
  constructor(canvas) {
    this.canvas = canvas;
    this.house = null;
    this.sensors = [];
    this.levelGroups = new Map();
    this.fieldTextures = new Map();
    this.selected = null;
    this.visibleLevels = new Set([0, 1, 2]);
    this.explode = 0;
    this.onSelect = () => {};

    this._initRenderer();
    this._initScene();
    this._initLights();
    this._resize();
    window.addEventListener("resize", () => this._resize());
  }

  // ── setup ────────────────────────────────────────────────────────────────

  _initRenderer() {
    this.renderer = new THREE.WebGLRenderer({
      canvas: this.canvas,
      antialias: true,
    });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  }

  _initScene() {
    this.scene = new THREE.Scene();
    this.scene.background = new THREE.Color(0x14160f);
    this.scene.fog = new THREE.Fog(0x14160f, 120, 320);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 800);
    this.camera.position.set(78, 62, 92);

    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.495;
    this.controls.target.set(15, 9, 16);
    // With damping, controls.update() keeps firing 'change' while it settles,
    // which is what keeps the render loop alive during a drag.
    this.controls.addEventListener("change", () => this.invalidate());

    this._dirty = true;

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.canvas.addEventListener("pointerdown", (e) => {
      if (this.editMode) this._beginDrag(e);
      else this._onClick(e);
    });
    this.canvas.addEventListener("pointermove", (e) => this._moveDrag(e));
    this.canvas.addEventListener("pointerup", (e) => this._endDrag(e));
    this.canvas.addEventListener("pointercancel", (e) => this._endDrag(e));
  }

  _initLights() {
    this.scene.add(new THREE.HemisphereLight(0xdfe8c8, 0x2a2c1e, 1.15));

    const sun = new THREE.DirectionalLight(0xfff4dc, 1.5);
    sun.position.set(60, 90, 40);
    sun.castShadow = true;
    sun.shadow.mapSize.set(2048, 2048);
    const cam = sun.shadow.camera;
    cam.left = -60; cam.right = 60; cam.top = 60; cam.bottom = -60;
    cam.near = 1; cam.far = 260;
    sun.shadow.bias = -0.0012;
    this.scene.add(sun);
    this.sun = sun;

    const fill = new THREE.DirectionalLight(0x9fb4d8, 0.4);
    fill.position.set(-50, 40, -30);
    this.scene.add(fill);
  }

  _resize() {
    const w = this.canvas.clientWidth || 1;
    const h = this.canvas.clientHeight || 1;
    this.renderer.setSize(w, h, false);
    this.camera.aspect = w / h;
    this.camera.updateProjectionMatrix();
    // Re-fit on resize so the model never ends up cropped or microscopic.
    if (this.house) this.frameBuilding();
    else this.invalidate();
  }

  // ── geometry ─────────────────────────────────────────────────────────────

  build(house) {
    this.house = house;
    const { footprint_ft: [fx, fz] } = house.meta;

    for (const level of house.levels) {
      const group = new THREE.Group();
      group.userData.level = level.index;
      group.userData.baseY = level.elevation;
      this.scene.add(group);
      this.levelGroups.set(level.index, group);

      this._buildFloorPlate(group, level, fx, fz);
      for (const room of level.rooms) this._buildRoom(group, room, level);
    }

    this._buildBase();
    this._buildSensors();
    this._applyExplode();
    this.frameBuilding();
  }

  _buildBase() {
    // Ground plane plus a plinth so the building does not float in the void.
    const [fx, fz] = this.house.meta.footprint_ft;
    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(400, 400),
      new THREE.MeshStandardMaterial({ color: 0x1b1e14, roughness: 1 })
    );
    ground.rotation.x = -Math.PI / 2;
    ground.position.set(fx / 2, -0.6, fz / 2);
    ground.receiveShadow = true;
    this.scene.add(ground);

    const grid = new THREE.GridHelper(400, 80, 0x2e3322, 0x232719);
    grid.position.y = -0.5;
    grid.material.transparent = true;
    grid.material.opacity = 0.5;
    this.scene.add(grid);

    const plinth = new THREE.Mesh(
      new THREE.BoxGeometry(fx + 3, 1.2, fz + 3),
      new THREE.MeshStandardMaterial({ color: 0x2a2d1f, roughness: 0.9 })
    );
    plinth.position.set(fx / 2, -1.2, fz / 2);
    plinth.receiveShadow = true;
    this.scene.add(plinth);
  }

  _buildFloorPlate(group, level, fx, fz) {
    // A canvas texture carrying the interpolated temperature field. Recomputed
    // whenever readings change rather than every frame.
    const canvas = document.createElement("canvas");
    canvas.width = 96;
    canvas.height = Math.round((96 * fz) / fx);
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    this.fieldTextures.set(level.index, { canvas, texture, level });

    const plate = new THREE.Mesh(
      new THREE.PlaneGeometry(fx, fz),
      new THREE.MeshStandardMaterial({
        map: texture,
        roughness: 0.95,
        metalness: 0,
        transparent: true,
      })
    );
    plate.rotation.x = -Math.PI / 2;
    plate.position.set(fx / 2, 0.06, fz / 2);
    plate.receiveShadow = true;
    plate.userData.isPlate = true;
    plate.userData.level = level.index;
    group.add(plate);
    this.plates = this.plates || new Map();
    this.plates.set(level.index, plate);
  }

  _buildRoom(group, room, level) {
    const wallColor = room.standing ? 0x8f9778 : 0x6a7059;
    const h = room.standing ? level.height : (this.house.meta.knee_wall ?? 4);

    const geo = new THREE.BoxGeometry(room.width, h, room.depth);
    const mesh = new THREE.Mesh(
      geo,
      new THREE.MeshStandardMaterial({
        color: wallColor,
        roughness: 0.85,
        transparent: true,
        opacity: 0.2,
        depthWrite: false,
      })
    );
    mesh.position.set(
      room.x + room.width / 2,
      h / 2,
      room.z + room.depth / 2
    );
    mesh.userData = { isRoom: true, room };
    group.add(mesh);

    // Crisp outline so rooms stay legible when the volume is nearly invisible.
    const edges = new THREE.LineSegments(
      new THREE.EdgesGeometry(geo),
      new THREE.LineBasicMaterial({
        color: room.standing ? 0xc9d3a4 : 0x8f9778,
        transparent: true,
        opacity: 0.5,
      })
    );
    edges.position.copy(mesh.position);
    edges.userData = { isRoom: true, room };
    group.add(edges);

    if (room.standing) {
      const label = this._makeLabel(room.name);
      label.position.set(
        room.x + room.width / 2,
        0.4,
        room.z + room.depth / 2
      );
      label.userData = { isRoom: true, room };
      group.add(label);
    }
  }

  _makeLabel(text) {
    const canvas = document.createElement("canvas");
    canvas.width = 512;
    canvas.height = 128;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, 512, 128);
    ctx.font = "600 44px ui-monospace, Menlo, monospace";
    ctx.fillStyle = "rgba(232, 240, 210, 0.88)";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(text, 256, 64);

    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: new THREE.CanvasTexture(canvas),
        transparent: true,
        depthTest: false,
        opacity: 0.75,
      })
    );
    sprite.scale.set(10, 2.5, 1);
    return sprite;
  }

  _buildSensors() {
    this.sensorGroup = new THREE.Group();
    this.scene.add(this.sensorGroup);
    this.sensorMeshes = new Map();
  }

  // ── readings ─────────────────────────────────────────────────────────────

  updateReadings(sensors) {
    this.sensors = sensors;
    const byId = new Map(sensors.map((s) => [s.id, s]));

    // Rebuild markers if the set changed, otherwise just restyle.
    for (const placement of this.house.sensors) {
      const reading = byId.get(placement.id);
      let mesh = this.sensorMeshes.get(placement.id);

      if (!mesh) {
        mesh = this._makeSensorMesh(placement);
        this.sensorGroup.add(mesh);
        this.sensorMeshes.set(placement.id, mesh);
      }
      this._styleSensor(mesh, placement, reading);
    }

    for (const level of this.house.levels) {
      this._paintField(level, byId);
    }
    this.invalidate();
  }

  _makeSensorMesh(placement) {
    const group = new THREE.Group();
    const bulb = new THREE.Mesh(
      new THREE.SphereGeometry(0.85, 24, 18),
      new THREE.MeshStandardMaterial({
        color: 0xdddddd,
        emissive: 0x000000,
        roughness: 0.35,
        metalness: 0.1,
      })
    );
    bulb.castShadow = true;
    bulb.userData = { isSensor: true, sensorId: placement.id };
    group.add(bulb);

    // Stem down to the floor plate.
    const stemH = placement.height_ft;
    const stem = new THREE.Mesh(
      new THREE.CylinderGeometry(0.07, 0.07, stemH, 8),
      new THREE.MeshStandardMaterial({ color: 0x6f7660, roughness: 0.8 })
    );
    stem.position.y = -stemH / 2;
    group.add(stem);

    const readout = this._makeReadout(placement.name);
    readout.position.y = 2.1;
    group.add(readout);

    group.userData = { bulb, readout, sensorId: placement.id };
    this.sensorGroup.add(group.clone ? group : group);
    return group;
  }

  _makeReadout(name) {
    const canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 128;
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false })
    );
    sprite.scale.set(7.5, 3.75, 1);
    sprite.userData = { canvas, ctx: canvas.getContext("2d"), texture };
    return sprite;
  }

  _styleSensor(mesh, placement, reading) {
    const { bulb, readout } = mesh.userData;
    const group = mesh;

    const levelGroup = this.levelGroups.get(placement.level);
    const y = (levelGroup?.userData.baseY ?? 0) + placement.height_ft;
    group.position.set(placement.x, y, placement.z);

    const hasTemp = typeof reading?.temperature === "number";
    const hasHum = typeof reading?.humidity === "number";
    const t = reading?.temperature;

    // Bulb colour follows the same ramp as the floor field.
    if (hasTemp) {
      const [r, g, b] = rampColor(this._normaliseTemp(t));
      bulb.material.color.setRGB(r / 255, g / 255, b / 255);
      bulb.material.emissive.setRGB(r / 255, g / 255, b / 255).multiplyScalar(0.55);
    } else {
      bulb.material.color.setHex(0x555a48);
      bulb.material.emissive.setHex(0x000000);
    }

    const stale = reading?.online === false;
    const lowBattery = typeof reading?.battery === "number" && reading.battery < 20;
    const selected = this.selected === placement.id;
    const scale = selected ? 1.5 : stale ? 0.85 : 1;
    bulb.scale.setScalar(scale);
    bulb.material.opacity = stale ? 0.5 : 1;

    const label = readout.userData;
    const { ctx, canvas, texture } = label;
    ctx.clearRect(0, 0, 256, 128);
    ctx.textAlign = "center";

    ctx.fillStyle = "rgba(12,14,8,0.72)";
    ctx.fillRect(0, 8, 256, 112);

    ctx.font = "600 30px ui-monospace, Menlo, monospace";
    ctx.fillStyle = selected ? "#eaf0c8" : "rgba(226,234,200,0.8)";
    ctx.fillText(placement.name, 128, 44);

    ctx.font = "700 46px ui-monospace, Menlo, monospace";
    if (hasTemp) ctx.fillStyle = "#f2f6dc";
    else ctx.fillStyle = "rgba(200,208,176,0.45)";
    ctx.fillText(hasTemp ? `${t.toFixed(1)}°` : "--.-°", 128, 92);

    if (hasHum) {
      ctx.font = "500 22px ui-monospace, Menlo, monospace";
      ctx.fillStyle = "rgba(150,190,200,0.9)";
      ctx.fillText(`${reading.humidity.toFixed(0)}% RH`, 128, 114);
    }
    if (lowBattery) {
      ctx.fillStyle = "#d98b3a";
      ctx.fillText("!", 244, 30);
    }
    if (stale) {
      ctx.fillStyle = "#c05a4a";
      ctx.fillText("OFFLINE", 128, 114);
    }
    texture.needsUpdate = true;
  }

  // ── IDW temperature field ────────────────────────────────────────────────

  _normaliseTemp(t) {
    // 16–30 °C spans the ramp. Comfortable indoor air sits mid-scale.
    return (t - 16) / 14;
  }

  _paintField(level, byId) {
    const entry = this.fieldTextures.get(level.index);
    if (!entry) return;
    const { canvas, texture } = entry;
    const ctx = canvas.getContext("2d");
    const [fx, fz] = this.house.meta.footprint_ft;

    const sensors = this.house.sensors
      .filter((s) => s.level === level.index)
      .map((s) => ({ ...s, ...(byId.get(s.id) || {}) }))
      .filter((s) => typeof s.temperature === "number");

    const img = ctx.createImageData(canvas.width, canvas.height);

    if (!sensors.length) {
      // No data on this level — neutral, so it is obvious rather than fake.
      for (let i = 0; i < img.data.length; i += 4) {
        img.data[i] = 40; img.data[i + 1] = 44; img.data[i + 2] = 32; img.data[i + 3] = 255;
      }
    } else {
      const P = 2.6;          // inverse-distance power
      const RADIUS = 26;     // ft beyond which a sensor stops contributing
      const EPS = 0.6;       // keeps the field from blowing up at a sensor

      for (let py = 0; py < canvas.height; py++) {
        const wz = ((py + 0.5) / canvas.height) * fz;
        for (let px = 0; px < canvas.width; px++) {
          const wx = ((px + 0.5) / canvas.width) * fx;

          let num = 0;
          let den = 0;
          for (const s of sensors) {
            const d = Math.hypot(wx - s.x, wz - s.z);
            if (d > RADIUS) continue;
            const w = 1 / Math.pow(d + EPS, P);
            num += w * s.temperature;
            den += w;
          }

          let t;
          if (den > 0) {
            t = num / den;
          } else {
            // Outside every sensor's radius: fall back to the nearest one.
            let best = Infinity;
            t = sensors[0].temperature;
            for (const s of sensors) {
              const d = Math.hypot(wx - s.x, wz - s.z);
              if (d < best) { best = d; t = s.temperature; }
            }
          }

          const [r, g, b] = rampColor(this._normaliseTemp(t));
          const o = (py * canvas.width + px) * 4;
          img.data[o] = r; img.data[o + 1] = g; img.data[o + 2] = b; img.data[o + 3] = 255;
        }
      }
    }

    ctx.putImageData(img, 0, 0);
    texture.needsUpdate = true;
  }

  // ── interaction ──────────────────────────────────────────────────────────

  _onClick(event) {
    this._setPointer(event);
    this.raycaster.setFromCamera(this.pointer, this.camera);

    const hits = this.raycaster.intersectObjects(
      [...this.sensorMeshes.values()].flatMap((g) => g.children), true
    );
    if (hits.length) {
      let node = hits[0].object;
      while (node && !node.userData?.sensorId) node = node.parent;
      if (node?.userData?.sensorId) {
        this.select(node.userData.sensorId);
        return;
      }
    }

    const roomHits = this.raycaster.intersectObjects(this.scene.children, true);
    for (const hit of roomHits) {
      let node = hit.object;
      while (node && !node.userData?.room) node = node.parent;
      if (node?.userData?.room) {
        this.onSelect({ type: "room", room: node.userData.room });
        return;
      }
    }
    this.select(null);
  }

  select(sensorId) {
    this.selected = sensorId;
    this.updateReadings(this.sensors);
    const placement = this.house.sensors.find((s) => s.id === sensorId);
    this.onSelect({ type: "sensor", sensor: placement });
  }

  setLevelVisible(index, visible) {
    if (visible) this.visibleLevels.add(index);
    else this.visibleLevels.delete(index);
    const group = this.levelGroups.get(index);
    if (group) group.visible = visible;
    this.invalidate();
  }

  setExplode(amount) {
    this.explode = amount;
    this._applyExplode();
    this.invalidate();
  }

  _applyExplode() {
    for (const [index, group] of this.levelGroups) {
      const base = group.userData.baseY;
      group.position.y = base + index * this.explode * 9;
    }
    for (const [id, mesh] of this.sensorMeshes) {
      const placement = this.house.sensors.find((s) => s.id === id);
      const group = this.levelGroups.get(placement.level);
      const y = group.position.y + placement.height_ft;
      mesh.position.y = y;
    }
  }

  focusSensor(sensorId) {
    const placement = this.house.sensors.find((s) => s.id === sensorId);
    if (!placement) return;
    const group = this.levelGroups.get(placement.level);
    const target = new THREE.Vector3(placement.x, group.position.y + 3, placement.z);
    this._animateTo(target);
  }

  _animateTo(target) {
    this._tween = {
      from: this.controls.target.clone(),
      to: target,
      start: performance.now(),
      duration: 650,
    };
  }

  /**
   * Draw a single frame.
   *
   * Rendering is on demand rather than a continuous rAF loop. The scene is
   * static apart from camera moves and data refreshes, so an unconditional
   * 60fps loop just burns a core and starves the compositor. Callers mark the
   * view dirty via `invalidate()`.
   */
  /**
   * Pull the camera back far enough to frame the whole building.
   *
   * Distance is solved from the bounding sphere and the *narrower* of the two
   * field-of-view angles, so the model stays fully visible in a short or narrow
   * window instead of overflowing it.
   */
  frameBuilding() {
    if (!this.house) return;
    const [fx, fz] = this.house.meta.footprint_ft;
    const height = this.house.meta.ridge_height_ft;

    const center = new THREE.Vector3(fx / 2, height * 0.42, fz / 2);
    const radius = 0.5 * Math.hypot(fx, height, fz);

    const vFov = (this.camera.fov * Math.PI) / 180;
    const hFov = 2 * Math.atan(Math.tan(vFov / 2) * this.camera.aspect);
    const limiting = Math.min(vFov, hFov);
    const distance = (radius / Math.sin(limiting / 2)) * 1.06;

    // Keep a consistent three-quarter view: up and off to one side.
    const dir = new THREE.Vector3(0.62, 0.55, 0.78).normalize();
    this.camera.position.copy(center).addScaledVector(dir, distance);
    this.camera.near = Math.max(0.5, distance * 0.02);
    this.camera.far = distance * 6;
    this.camera.updateProjectionMatrix();

    this.controls.target.copy(center);
    this.controls.maxDistance = distance * 4;
    this.controls.update();
    this.invalidate();
  }

  /**
   * Move sensors by dragging them across their floor plate.
   *
   * Positions are guesses until someone stands in the room with a tape measure,
   * so this is the fastest way to correct them. The drag is constrained to the
   * XZ plane of the sensor's own level, and the nearest room is reported on
   * drop so the placement can be sanity-checked before saving.
   */
  setEditMode(on) {
    this.editMode = !!on;
    this.canvas.style.cursor = on ? "crosshair" : "";
    for (const [id, mesh] of this.sensorMeshes) {
      const { bulb } = mesh.userData;
      bulb.material.emissiveIntensity = this.editMode ? 0.9 : 0.55;
      mesh.scale.setScalar(this.editMode ? 1.25 : 1);
    }
    this.invalidate();
  }

  /** Room whose rectangle contains (x, z) on *level*, or null. */
  roomAt(level, x, z) {
    const lv = this.house.levels.find((l) => l.index === level);
    if (!lv) return null;
    return (
      lv.rooms.find(
        (r) => x >= r.x && x <= r.x + r.width && z >= r.z && z <= r.z + r.depth
      ) || null
    );
  }

  _beginDrag(event) {
    if (!this.editMode) return;
    const hit = this._pickSensor(event);
    if (!hit) return;

    this._drag = {
      sensorId: hit,
      plane: new THREE.Plane(new THREE.Vector3(0, 1, 0), 0),
      offset: new THREE.Vector3(),
    };
    const mesh = this.sensorMeshes.get(hit);
    this._drag.plane.constant = -mesh.position.y;
    this.raycaster.setFromCamera(this.pointer, this.camera);
    this.raycaster.ray.intersectPlane(this._drag.plane, this._drag.offset);
    this._drag.offset.sub(mesh.position);
    this.controls.enabled = false;
    this.canvas.setPointerCapture(event.pointerId);
  }

  _moveDrag(event) {
    if (!this._drag) return;
    const mesh = this.sensorMeshes.get(this._drag.sensorId);
    if (!mesh) return;

    this._setPointer(event);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const point = new THREE.Vector3();
    if (!this.raycaster.ray.intersectPlane(this._drag.plane, point)) return;

    point.sub(this._drag.offset);
    const [fx, fz] = this.house.meta.footprint_ft;
    // Clamp so a drag can never fling a sensor off the building.
    point.x = Math.max(0.5, Math.min(fx - 0.5, point.x));
    point.z = Math.max(0.5, Math.min(fz - 0.5, point.z));
    mesh.position.x = point.x;
    mesh.position.z = point.z;
    this.invalidate();
  }

  _endDrag(event) {
    if (!this._drag) return;
    const { sensorId } = this._drag;
    const mesh = this.sensorMeshes.get(sensorId);
    this._drag = null;
    this.controls.enabled = true;
    if (event?.pointerId != null) {
      try { this.canvas.releasePointerCapture(event.pointerId); } catch { /* already released */ }
    }
    if (!mesh) return;

    const placement = this.house.sensors.find((s) => s.id === sensorId);
    if (!placement) return;
    const room = this.roomAt(placement.level, mesh.position.x, mesh.position.z);
    const result = this.onEdit({
      id: sensorId,
      x: +mesh.position.x.toFixed(2),
      z: +mesh.position.z.toFixed(2),
      room: room ? room.name : null,
      roomId: room ? room.id : null,
    });
    if (result === false) this._restorePlacement(placement, mesh);
  }

  _restorePlacement(placement, mesh) {
    const group = this.levelGroups.get(placement.level);
    mesh.position.set(placement.x, group.position.y + placement.height_ft, placement.z);
    this.invalidate();
  }

  /**
   * Commit a position into the in-memory model.
   *
   * `this.house` is the plain JSON from /api/house, not dataclass instances, so
   * entries are spread rather than serialised. The level's explosion offset is
   * recomputed on the next _applyExplode/invalidate cycle.
   */
  applyPlacement(sensorId, x, z) {
    const index = this.house.sensors.findIndex((s) => s.id === sensorId);
    if (index < 0) return null;
    const current = this.house.sensors[index];
    const room = this.roomAt(current.level, x, z);
    const updated = {
      ...current,
      x,
      z,
      room_id: room ? room.id : current.room_id,
    };
    this.house.sensors[index] = updated;
    return updated;
  }

  _pickSensor(event) {
    this._setPointer(event);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    const bulbs = [...this.sensorMeshes.values()].map((m) => m.userData.bulb);
    const hits = this.raycaster.intersectObjects(bulbs, false);
    return hits.length ? hits[0].object.userData.sensorId : null;
  }

  _setPointer(event) {
    const rect = this.canvas.getBoundingClientRect();
    this.pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    this.pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
  }

  render() {
    if (!this._dirty) return;
    this._dirty = false;

    if (this._tween) {
      const k = Math.min(1, (performance.now() - this._tween.start) / this._tween.duration);
      const e = 1 - Math.pow(1 - k, 3);
      this.controls.target.lerpVectors(this._tween.from, this._tween.to, e);
      if (k >= 1) this._tween = null;
      else this._dirty = true; // keep animating until the tween lands
    }

    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  }

  /** Request one more frame. Safe to call as often as you like. */
  invalidate() {
    this._dirty = true;
    if (!this._loopRunning) this._startLoop();
  }

  _startLoop() {
    this._loopRunning = true;
    const tick = () => {
      this.render();
      // Keep the loop alive only while there is something to animate.
      if (this._dirty || this._tween) requestAnimationFrame(tick);
      else this._loopRunning = false;
    };
    requestAnimationFrame(tick);
  }
}
