import * as THREE from "three";

// ==================================================
// 0. SETTINGS (you can tweak these numbers)
// ==================================================

const EARTH_RADIUS = 2.0;

const AUTO_SPEED = 0.10;         // rad / second -> slow automatic rotation
const CLOUD_EXTRA_SPEED = 0.012; // clouds drift a bit faster than the ground
const SATELLITE_SPEED = 0.0;     // 0 = fixed satellite. Try 0.15 to make it orbit.

const BASE_TILT_X = 0.28;        // camera looks slightly from above
const AXIAL_TILT_Z = 0.41;       // real Earth tilt (about 23.5 degrees)
const MAX_TILT_OFFSET = 0.9;     // how far the user can tilt up/down

// Extra textures come from the three.js repo (pinned version).
// They are also used as a BACKUP if your own earth.jpg is not found.
const TEX_BASE =
    "https://cdn.jsdelivr.net/gh/mrdoob/three.js@r160/examples/textures/planets/";

// Wildfire regions shown on the globe. The numbers come from NASA FIRMS via /api/fire/globe/ (last 24 h);
// nothing is simulated. `key` must match satellite/globe.py.
const REGIONS = [
    { key: "california", name: "California, USA",             lat: 37.0,  lon: -120.0, biome: "Mediterranean forest" },
    { key: "alberta", name: "Alberta, Canada",             lat: 56.0,  lon: -115.0, biome: "Boreal forest" },
    { key: "amazon", name: "Amazon Basin, Brazil",        lat: -6.0,  lon: -58.0,  biome: "Tropical rainforest" },
    { key: "morocco", name: "Rif & Middle Atlas, Morocco", lat: 34.5,  lon: -5.0,   biome: "Mountain forest" },
    { key: "east_med", name: "Eastern Mediterranean",       lat: 38.0,  lon: 27.0,   biome: "Pine forest & scrub" },
    { key: "africa", name: "Central African Savanna",     lat: 7.0,   lon: 22.0,   biome: "Savanna" },
    { key: "siberia", name: "Siberia, Russia",             lat: 60.0,  lon: 110.0,  biome: "Taiga" },
    { key: "borneo", name: "Borneo, Indonesia",           lat: -1.5,  lon: 114.0,  biome: "Peat forest" },
    { key: "nsw", name: "New South Wales, Australia",  lat: -32.5, lon: 149.5,  biome: "Eucalyptus forest" }
];

// ==================================================
// 1. HTML ELEMENTS
// ==================================================

const container = document.getElementById("earth-scene");
const canvas = document.getElementById("earth-canvas");

if (!container || !canvas) {
    throw new Error("PLEIADES: Earth container or canvas not found.");
}

const visual = container.parentElement; // .hero-visual (for UI overlays)

// ==================================================
// 2. SCENE AND CAMERA
// ==================================================

const scene = new THREE.Scene();

const CAMERA_FOV = 38;
const CAMERA_Z = 8.6;

const camera = new THREE.PerspectiveCamera(
    CAMERA_FOV,
    container.clientWidth / container.clientHeight,
    0.1,
    100
);

camera.position.set(0, 0, CAMERA_Z);

// ==================================================
// 3. RENDERER
// ==================================================

const renderer = new THREE.WebGLRenderer({
    canvas: canvas,
    alpha: true,
    antialias: true,
    powerPreference: "high-performance"
});

renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(container.clientWidth, container.clientHeight);

renderer.outputColorSpace = THREE.SRGBColorSpace;
renderer.toneMapping = THREE.NoToneMapping;

const maxAniso = renderer.capabilities.getMaxAnisotropy();

// ==================================================
// 4. GROUPS
//    tiltGroup  -> up/down tilt (mouse Y) + Earth axis lean
//    earthGroup -> spin around the axis (mouse X)
// ==================================================

const tiltGroup = new THREE.Group();
const earthGroup = new THREE.Group();

tiltGroup.rotation.x = BASE_TILT_X;
tiltGroup.rotation.z = AXIAL_TILT_Z;

tiltGroup.add(earthGroup);
scene.add(tiltGroup);

// ==================================================
// 5. TEXTURES (with backup URLs)
// ==================================================

const textureLoader = new THREE.TextureLoader();
textureLoader.setCrossOrigin("anonymous");

function loadTexture(urls, srgb, onLoad) {

    const list = Array.isArray(urls) ? urls : [urls];

    const tryLoad = (i) => {

        textureLoader.load(
            list[i],

            (texture) => {
                texture.colorSpace = srgb
                    ? THREE.SRGBColorSpace
                    : THREE.NoColorSpace;
                texture.anisotropy = maxAniso;
                texture.needsUpdate = true;
                onLoad(texture);
            },

            undefined,

            () => {
                console.warn("PLEIADES: texture not found:", list[i]);
                if (i + 1 < list.length) tryLoad(i + 1);
            }
        );
    };

    tryLoad(0);
}

function blackTexture() {
    const t = new THREE.DataTexture(
        new Uint8Array([0, 0, 0, 255]), 1, 1
    );
    t.colorSpace = THREE.NoColorSpace;
    t.needsUpdate = true;
    return t;
}

// ==================================================
// 6. EARTH (day + night lights + ocean shine)
// ==================================================

const SUN_DIRECTION = new THREE.Vector3(4, 2, 6).normalize();

const earthUniforms = {
    dayMap:   { value: blackTexture() },
    nightMap: { value: blackTexture() },
    specMap:  { value: blackTexture() },
    sunDir:   { value: SUN_DIRECTION.clone() }
};

// Your own map first, NASA-style backup second
loadTexture(
    ["/static/images/earth.jpg", TEX_BASE + "earth_atmos_2048.jpg"],
    true,
    (t) => { earthUniforms.dayMap.value = t; }
);

loadTexture(TEX_BASE + "earth_lights_2048.png", true, (t) => {
    earthUniforms.nightMap.value = t;
});

loadTexture(TEX_BASE + "earth_specular_2048.jpg", false, (t) => {
    earthUniforms.specMap.value = t;
});

const earthMaterial = new THREE.ShaderMaterial({

    uniforms: earthUniforms,

    vertexShader: `
        varying vec2 vUv;
        varying vec3 vNormalV;
        varying vec3 vViewDir;

        void main() {
            vUv = uv;
            vNormalV = normalize(normalMatrix * normal);

            vec4 mv = modelViewMatrix * vec4(position, 1.0);
            vViewDir = normalize(-mv.xyz);

            gl_Position = projectionMatrix * mv;
        }
    `,

    fragmentShader: `
        uniform sampler2D dayMap;
        uniform sampler2D nightMap;
        uniform sampler2D specMap;
        uniform vec3 sunDir;

        varying vec2 vUv;
        varying vec3 vNormalV;
        varying vec3 vViewDir;

        void main() {

            vec3 N = normalize(vNormalV);
            vec3 V = normalize(vViewDir);
            vec3 L = normalize(sunDir);

            float ndl = dot(N, L);

            // 0 = night, 1 = day (soft sunrise / sunset band)
            float dayMix = smoothstep(-0.22, 0.28, ndl);

            vec3 dayTex = texture2D(dayMap, vUv).rgb;

            // Daylight side
            vec3 dayColor = dayTex * (0.16 + 1.05 * max(ndl, 0.0));

            // Night side: dim blue ground + city lights
            vec3 lights = texture2D(nightMap, vUv).rgb;
            vec3 nightColor =
                dayTex * vec3(0.035, 0.06, 0.12)
                + lights * vec3(1.0, 0.72, 0.38) * 1.4;

            vec3 color = mix(nightColor, dayColor, dayMix);

            // Warm glow along the day / night line
            float twilight = exp(-ndl * ndl / 0.015);
            color += vec3(1.0, 0.45, 0.2) * twilight * 0.08;

            // Ocean shine (soft, not a hot spot)
            float oceanMask = texture2D(specMap, vUv).r;
            vec3 H = normalize(L + V);
            float spec = pow(max(dot(N, H), 0.0), 38.0);
            color += vec3(0.7, 0.85, 1.0) * spec * oceanMask * dayMix * 0.32;

            // Blue edge light
            float rim = pow(1.0 - max(dot(N, V), 0.0), 3.0);
            color += vec3(0.10, 0.36, 0.90) * rim * (0.18 + 0.40 * dayMix);

            gl_FragColor = vec4(color, 1.0);

            #include <colorspace_fragment>
        }
    `
});

const earth = new THREE.Mesh(
    new THREE.SphereGeometry(EARTH_RADIUS, 192, 192),
    earthMaterial
);

earthGroup.add(earth);

// ==================================================
// 7. CLOUDS (added only if the texture loads)
// ==================================================

let clouds = null;

loadTexture(TEX_BASE + "earth_clouds_1024.png", true, (texture) => {

    clouds = new THREE.Mesh(
        new THREE.SphereGeometry(EARTH_RADIUS * 1.012, 128, 128),
        new THREE.MeshLambertMaterial({
            map: texture,
            transparent: true,
            opacity: 0.75,
            depthWrite: false
        })
    );

    clouds.visible = layers.clouds;

    earthGroup.add(clouds);
});

// ==================================================
// 8. LIGHTS (used by clouds, satellite, orbits)
// ==================================================

scene.add(new THREE.AmbientLight(0xffffff, 0.5));

const sunlight = new THREE.DirectionalLight(0xffffff, 2.6);
sunlight.position.copy(SUN_DIRECTION).multiplyScalar(10);
scene.add(sunlight);

const blueLight = new THREE.PointLight(0x168bff, 0.8, 30);
blueLight.position.set(-6, 1, -4);
scene.add(blueLight);

// ==================================================
// 9. ATMOSPHERE GLOW (thin and soft)
// ==================================================

const atmosphere = new THREE.Mesh(
    new THREE.SphereGeometry(EARTH_RADIUS * 1.1, 128, 128),

    new THREE.ShaderMaterial({

        vertexShader: `
            varying vec3 vNormal;

            void main() {
                vNormal = normalize(normalMatrix * normal);
                gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
            }
        `,

        fragmentShader: `
            varying vec3 vNormal;

            void main() {

                float intensity = pow(
                    max(0.0, 0.70 - dot(vNormal, vec3(0.0, 0.0, 1.0))),
                    4.0
                );

                gl_FragColor = vec4(0.14, 0.52, 1.0, 1.0) * intensity * 1.15;
            }
        `,

        blending: THREE.AdditiveBlending,
        side: THREE.BackSide,
        transparent: true,
        depthWrite: false
    })
);

scene.add(atmosphere);

// ==================================================
// 10. SMOOTH ORBITAL PATHS
// ==================================================

function createOrbit(radius, color, tiltX, tiltY) {

    const points = [];
    const segments = 512;

    for (let i = 0; i < segments; i++) {

        const angle = (i / segments) * Math.PI * 2;

        points.push(
            new THREE.Vector3(
                Math.cos(angle) * radius,
                Math.sin(angle) * radius,
                0
            )
        );
    }

    const curve = new THREE.CatmullRomCurve3(points, true, "centripetal");

    const orbitGroup = new THREE.Group();

    orbitGroup.rotation.x = tiltX;
    orbitGroup.rotation.y = tiltY;

    orbitGroup.add(new THREE.Mesh(
        new THREE.TubeGeometry(curve, 512, 0.02, 8, true),
        new THREE.MeshBasicMaterial({
            color: color,
            transparent: true,
            opacity: 0.10,
            depthWrite: false
        })
    ));

    orbitGroup.add(new THREE.Mesh(
        new THREE.TubeGeometry(curve, 512, 0.005, 8, true),
        new THREE.MeshBasicMaterial({
            color: color,
            transparent: true,
            opacity: 0.8,
            depthWrite: false
        })
    ));

    scene.add(orbitGroup);

    return orbitGroup;
}

const orbitOne = createOrbit(2.45, 0x28aaff, 0.8, 0.3);

const orbits = [
    orbitOne,
    createOrbit(2.68, 0x8b70ff, 1.1, 0.5),
    createOrbit(2.90, 0x28aaff, 0.4, 1.0)
];

// ==================================================
// 11. SATELLITE (sits exactly on the first orbit)
// ==================================================

const satellite = new THREE.Group();

satellite.add(new THREE.Mesh(
    new THREE.BoxGeometry(0.15, 0.15, 0.20),
    new THREE.MeshStandardMaterial({
        color: 0xdce7f5,
        metalness: 0.8,
        roughness: 0.3
    })
));

const panelGeometry = new THREE.BoxGeometry(0.38, 0.025, 0.18);

const panelMaterial = new THREE.MeshStandardMaterial({
    color: 0x1257a5,
    metalness: 0.5,
    roughness: 0.35,
    emissive: 0x0a2a55,
    emissiveIntensity: 0.6
});

const leftPanel = new THREE.Mesh(panelGeometry, panelMaterial);
leftPanel.position.x = -0.27;
satellite.add(leftPanel);

const rightPanel = new THREE.Mesh(panelGeometry, panelMaterial);
rightPanel.position.x = 0.27;
satellite.add(rightPanel);

orbitOne.add(satellite);

let satelliteAngle = 0;
satellite.position.set(2.45, 0, 0);

// ==================================================
// 12. LAYER STATE (controlled by the buttons)
// ==================================================

const layers = {
    hotspots: true,
    clouds: true,
    orbits: true,
    rotate: true
};

// ==================================================
// 13. WILDFIRE HOTSPOTS (glowing dots on the globe)
// ==================================================

// latitude / longitude -> point on the sphere (matches the texture)
function latLonToVector3(lat, lon, radius) {

    const la = THREE.MathUtils.degToRad(lat);
    const lo = THREE.MathUtils.degToRad(lon);

    return new THREE.Vector3(
        radius * Math.cos(la) * Math.cos(lo),
        radius * Math.sin(la),
        -radius * Math.cos(la) * Math.sin(lo)
    );
}

function makeGlowTexture() {

    const c = document.createElement("canvas");
    c.width = c.height = 128;

    const g = c.getContext("2d");
    const grad = g.createRadialGradient(64, 64, 0, 64, 64, 64);

    grad.addColorStop(0.00, "rgba(255,255,255,1)");
    grad.addColorStop(0.16, "rgba(255,196,120,0.95)");
    grad.addColorStop(0.42, "rgba(255,110,40,0.38)");
    grad.addColorStop(1.00, "rgba(255,80,20,0)");

    g.fillStyle = grad;
    g.fillRect(0, 0, 128, 128);

    const t = new THREE.CanvasTexture(c);
    t.colorSpace = THREE.SRGBColorSpace;
    return t;
}

const glowTexture = makeGlowTexture();
const hotspotGroup = new THREE.Group();
earthGroup.add(hotspotGroup);

const hotspots = REGIONS.map((region, index) => {

    const position = latLonToVector3(region.lat, region.lon, EARTH_RADIUS * 1.012);

    const glow = new THREE.Sprite(new THREE.SpriteMaterial({
        map: glowTexture,
        blending: THREE.AdditiveBlending,
        transparent: true,
        depthWrite: false
    }));

    glow.position.copy(position);
    glow.scale.setScalar(0.34);

    // invisible bigger sphere = easier to hover / click
    const hit = new THREE.Mesh(
        new THREE.SphereGeometry(0.13, 12, 12),
        new THREE.MeshBasicMaterial({
            transparent: true,
            opacity: 0,
            depthWrite: false
        })
    );

    hit.position.copy(position);

    hotspotGroup.add(glow, hit);

    return {
        region,
        glow,
        hit,
        phase: index * 1.3,
        count: null,      // filled from /api/fire/globe/ (null = not loaded yet)
        frp: null,
        last: null
    };
});

// ==================================================
// 14. FULL PAGE STARS
// ==================================================

const starsCanvas = document.createElement("canvas");
starsCanvas.id = "PLEIADES-stars-canvas";
document.body.prepend(starsCanvas);

const starsScene = new THREE.Scene();

const starsCamera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, 10);
starsCamera.position.z = 5;

const starsRenderer = new THREE.WebGLRenderer({
    canvas: starsCanvas,
    alpha: true,
    antialias: true
});

starsRenderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
starsRenderer.setClearColor(0x000000, 0);

let starLayers = [];
let lastStarsW = 0;
let lastStarsH = 0;

function makeStarLayer(count, size, color, opacity, width, height) {

    const positions = [];

    for (let i = 0; i < count; i++) {
        positions.push(
            (Math.random() - 0.5) * width,
            (Math.random() - 0.5) * height,
            0
        );
    }

    const geometry = new THREE.BufferGeometry();

    geometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(positions, 3)
    );

    const material = new THREE.PointsMaterial({
        color: color,
        size: size,
        transparent: true,
        opacity: opacity,
        sizeAttenuation: false,
        depthWrite: false
    });

    const points = new THREE.Points(geometry, material);

    starsScene.add(points);

    return points;
}

function createStars() {

    const width = window.innerWidth;
    const height = window.innerHeight;

    if (width === lastStarsW && height === lastStarsH) return;

    lastStarsW = width;
    lastStarsH = height;

    starLayers.forEach((layer) => {
        starsScene.remove(layer);
        layer.geometry.dispose();
        layer.material.dispose();
    });

    starLayers = [
        makeStarLayer(2200, 1.2, 0xffffff, 0.70, width, height),
        makeStarLayer(260,  2.0, 0xcfe0ff, 0.85, width, height),
        makeStarLayer(50,   2.8, 0xffe3c4, 0.90, width, height)
    ];

    starsCamera.left = -width / 2;
    starsCamera.right = width / 2;
    starsCamera.top = height / 2;
    starsCamera.bottom = -height / 2;
    starsCamera.updateProjectionMatrix();

    starsRenderer.setSize(width, height);
}

createStars();

// ==================================================
// 15. UI OVERLAYS (created here, styled in style.css)
// ==================================================

// Tooltip (follows the mouse over a hotspot)

const tooltip = document.createElement("div");
tooltip.className = "globe-tooltip";
tooltip.hidden = true;
visual.appendChild(tooltip);

// Region panel (opens when you click a hotspot)

const panel = document.createElement("aside");
panel.className = "region-panel";
panel.hidden = true;

panel.innerHTML = `
    <button class="region-close" type="button" aria-label="Close panel">×</button>
    <h4 class="region-name"></h4>
    <p class="region-meta"></p>
    <div class="region-stats">
        <div><strong data-stat="count">-</strong><small>Hotspots, last 24 h</small></div>
        <div><strong data-stat="conf">-</strong><small>Total FRP (MW)</small></div>
    </div>
    <p class="region-note">NASA FIRMS · MODIS + VIIRS · live data</p>
`;

visual.appendChild(panel);

const panelName  = panel.querySelector(".region-name");
const panelMeta  = panel.querySelector(".region-meta");
const panelCount = panel.querySelector('[data-stat="count"]');
const panelConf  = panel.querySelector('[data-stat="conf"]');
const panelNote  = panel.querySelector(".region-note");

// Hint + layer buttons

const controls = document.createElement("div");
controls.className = "globe-controls";

controls.innerHTML = `
    <span class="globe-hint">Drag to rotate · click a glowing dot</span>
    <div class="globe-toggles">
        <button type="button" class="globe-toggle" data-layer="hotspots" aria-pressed="true">Hotspots</button>
        <button type="button" class="globe-toggle" data-layer="clouds"   aria-pressed="true">Clouds</button>
        <button type="button" class="globe-toggle" data-layer="orbits"   aria-pressed="true">Orbits</button>
        <button type="button" class="globe-toggle" data-layer="rotate"   aria-pressed="true">Auto-rotate</button>
        <button type="button" class="globe-refresh">Refresh live data</button>
    </div>
`;

visual.appendChild(controls);

const hint = controls.querySelector(".globe-hint");
const refreshButton = controls.querySelector(".globe-refresh");
refreshButton.addEventListener("click", refreshLive);

function applyLayers() {

    hotspotGroup.visible = layers.hotspots;

    if (clouds) clouds.visible = layers.clouds;

    orbits.forEach((o) => { o.visible = layers.orbits; });

    if (!layers.hotspots) closePanel();
}

controls.querySelectorAll(".globe-toggle").forEach((button) => {

    button.addEventListener("click", () => {

        const key = button.dataset.layer;

        layers[key] = !layers[key];

        button.setAttribute("aria-pressed", String(layers[key]));

        applyLayers();
    });
});

// ==================================================
// 16. SELECT A REGION  (fly-to)
// ==================================================

let selected = null;   // currently selected hotspot
let focus = null;      // { ry, tx } -> where the globe should turn to

function formatCoords(lat, lon) {
    return (
        Math.abs(lat).toFixed(1) + "°" + (lat >= 0 ? "N" : "S") + ", " +
        Math.abs(lon).toFixed(1) + "°" + (lon >= 0 ? "E" : "W")
    );
}

function updatePanelNumbers() {
    if (!selected) return;
    panelCount.textContent = selected.count === null ? "-" : selected.count;
    panelConf.textContent = selected.frp === null ? "-" : Math.round(selected.frp).toLocaleString();
    panelNote.textContent = LIVE.note;
}

function selectHotspot(spot) {

    selected = spot;

    const v = latLonToVector3(spot.region.lat, spot.region.lon, 1);

    focus = {
        ry: Math.atan2(-v.x, v.z),
        tx: THREE.MathUtils.degToRad(spot.region.lat) * 0.85
    };

    panelName.textContent = spot.region.name;
    panelMeta.textContent =
        formatCoords(spot.region.lat, spot.region.lon) + " · " + spot.region.biome;

    updatePanelNumbers();

    panel.hidden = false;
    tooltip.hidden = true;
}

function closePanel() {
    selected = null;
    focus = null;
    panel.hidden = true;
}

panel.querySelector(".region-close").addEventListener("click", closePanel);

window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePanel();
});

// ==================================================
// LIVE DATA (NASA FIRMS through the PLEIADES API)
// ==================================================

const LIVE = { note: "NASA FIRMS · MODIS + VIIRS · loading…", busy: false };

function loadLive() {
    const url = container.dataset.globeUrl;
    if (!url) return Promise.resolve();
    return fetch(url, { headers: { Accept: "application/json" } })
        .then((r) => r.json())
        .then((d) => {
            const byKey = {};
            d.regions.forEach((r) => { byKey[r.key] = r; });
            hotspots.forEach((spot) => {
                const r = byKey[spot.region.key];
                if (!r) return;
                spot.count = r.count;
                spot.frp = r.frp_mw;
                spot.last = r.last_detection_utc;
            });
            LIVE.note = d.has_real_data
                ? "NASA FIRMS · MODIS + VIIRS · last 24 h (UTC)"
                : "No NASA data stored yet · press “Refresh live data”";
            updatePanelNumbers();
            return d;
        })
        .catch(() => { LIVE.note = "Live data unavailable"; updatePanelNumbers(); });
}

function csrfToken() {
    const m = document.cookie.match(/(?:^|; )csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
}

function refreshLive() {
    const url = container.dataset.refreshUrl;
    if (!url || LIVE.busy) return;
    LIVE.busy = true;
    refreshButton.disabled = true;
    refreshButton.textContent = "Contacting NASA FIRMS…";
    fetch(url, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(), Accept: "application/json" },
        body: JSON.stringify({ target: "globe" })
    })
        .then((r) => r.json().catch(() => ({})))
        .then((d) => {
            if (d.status !== "started") throw new Error(d.message || d.error || "failed");
            // the server pulls the regions in the background: poll for ~90 s
            let tries = 0;
            const timer = setInterval(() => {
                loadLive();
                if (++tries >= 18) { clearInterval(timer); finishRefresh("Refresh live data"); }
            }, 5000);
        })
        .catch((e) => finishRefresh("Refresh live data", e.message));
}

function finishRefresh(label, error) {
    LIVE.busy = false;
    refreshButton.disabled = false;
    refreshButton.textContent = label;
    if (error) { LIVE.note = error; updatePanelNumbers(); }
}

// ==================================================
// 17. PICKING (hover + click on hotspots)
// ==================================================

const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();

function pickHotspot(event) {

    if (!layers.hotspots) return null;

    const rect = canvas.getBoundingClientRect();

    pointer.set(
        ((event.clientX - rect.left) / rect.width) * 2 - 1,
        -((event.clientY - rect.top) / rect.height) * 2 + 1
    );

    raycaster.setFromCamera(pointer, camera);

    // include the Earth so dots on the far side cannot be picked
    const hits = raycaster.intersectObjects(
        [earth, ...hotspots.map((h) => h.hit)],
        false
    );

    if (!hits.length) return null;

    return hotspots.find((h) => h.hit === hits[0].object) || null;
}

function updateHover(event) {

    const spot = pickHotspot(event);

    canvas.style.cursor = spot ? "pointer" : "";

    if (!spot) {
        tooltip.hidden = true;
        return;
    }

    const rect = visual.getBoundingClientRect();

    tooltip.textContent = spot.region.name;
    tooltip.style.left = (event.clientX - rect.left + 14) + "px";
    tooltip.style.top  = (event.clientY - rect.top - 8) + "px";
    tooltip.hidden = false;
}

// ==================================================
// 18. MOUSE / TOUCH CONTROL
//     The planet follows your finger or mouse,
//     keeps spinning a little after you let go,
//     then returns to its slow rotation.
// ==================================================

let isDragging = false;

let previousX = 0;
let previousY = 0;
let downX = 0;
let downY = 0;
let downTime = 0;
let lastMoveTime = 0;

let spinVelocity = AUTO_SPEED;
let radPerPixel = 0.006;

function updateDragFactor() {

    const visibleHeight =
        2 * Math.tan(THREE.MathUtils.degToRad(CAMERA_FOV / 2)) * CAMERA_Z;

    const earthPixelRadius =
        (EARTH_RADIUS / visibleHeight) * container.clientHeight;

    // 1 pixel of mouse = the surface moves about 1 pixel
    radPerPixel = 1 / Math.max(earthPixelRadius, 50);
}

updateDragFactor();

const minTilt = BASE_TILT_X - MAX_TILT_OFFSET;
const maxTilt = BASE_TILT_X + MAX_TILT_OFFSET;

canvas.addEventListener("pointerdown", (event) => {

    isDragging = true;
    focus = null; // the user takes control again

    previousX = downX = event.clientX;
    previousY = downY = event.clientY;
    downTime = lastMoveTime = performance.now();

    spinVelocity = 0;

    hint.classList.add("hide");
    tooltip.hidden = true;

    canvas.setPointerCapture(event.pointerId);
});

canvas.addEventListener("pointermove", (event) => {

    if (!isDragging) {
        updateHover(event);
        return;
    }

    const now = performance.now();
    const dt = Math.max((now - lastMoveTime) / 1000, 0.001);

    const deltaX = event.clientX - previousX;
    const deltaY = event.clientY - previousY;

    earthGroup.rotation.y += deltaX * radPerPixel;

    tiltGroup.rotation.x = THREE.MathUtils.clamp(
        tiltGroup.rotation.x + deltaY * radPerPixel,
        minTilt,
        maxTilt
    );

    const instant = (deltaX * radPerPixel) / dt;

    spinVelocity = THREE.MathUtils.clamp(
        THREE.MathUtils.lerp(spinVelocity, instant, 0.5),
        -9,
        9
    );

    previousX = event.clientX;
    previousY = event.clientY;
    lastMoveTime = now;
});

function stopDragging() {

    if (!isDragging) return;

    isDragging = false;

    if (performance.now() - lastMoveTime > 90) {
        spinVelocity = 0;
    }
}

canvas.addEventListener("pointerup", (event) => {

    // a short click without movement = click on the globe
    const moved = Math.hypot(event.clientX - downX, event.clientY - downY);
    const quick = performance.now() - downTime < 400;

    if (isDragging && moved < 6 && quick) {

        const spot = pickHotspot(event);

        if (spot) {
            selectHotspot(spot);
            spinVelocity = 0;
        } else if (selected) {
            closePanel();
        }
    }

    stopDragging();
});

canvas.addEventListener("pointercancel", stopDragging);
canvas.addEventListener("lostpointercapture", stopDragging);

canvas.addEventListener("pointerleave", () => {
    tooltip.hidden = true;
    canvas.style.cursor = "";
});

// ==================================================
// 19. RESPONSIVE RESIZE
// ==================================================

function resizeScene() {

    const width = container.clientWidth;
    const height = container.clientHeight;

    if (width === 0 || height === 0) return;

    camera.aspect = width / height;
    camera.updateProjectionMatrix();

    renderer.setSize(width, height);

    updateDragFactor();
    createStars();
}

window.addEventListener("resize", resizeScene);

// ==================================================
// 20. ANIMATION
// ==================================================

let lastTime = performance.now();

function wrapAngle(a) {
    return Math.atan2(Math.sin(a), Math.cos(a));
}

function animate(now) {

    requestAnimationFrame(animate);

    const dt = Math.min((now - lastTime) / 1000, 0.05);
    lastTime = now;

    const t = now / 1000;

    if (!isDragging) {

        if (focus) {

            // fly to the selected region and stay there
            const k = 1 - Math.exp(-dt * 3.2);

            earthGroup.rotation.y +=
                wrapAngle(focus.ry - earthGroup.rotation.y) * k;

            tiltGroup.rotation.x += (focus.tx - tiltGroup.rotation.x) * k;
            tiltGroup.rotation.z += (0 - tiltGroup.rotation.z) * k;

            spinVelocity = 0;

        } else {

            const target = layers.rotate ? AUTO_SPEED : 0;

            spinVelocity += (target - spinVelocity) * (1 - Math.exp(-dt * 1.3));

            earthGroup.rotation.y += spinVelocity * dt;

            tiltGroup.rotation.x +=
                (BASE_TILT_X - tiltGroup.rotation.x) * (1 - Math.exp(-dt * 0.9));

            tiltGroup.rotation.z +=
                (AXIAL_TILT_Z - tiltGroup.rotation.z) * (1 - Math.exp(-dt * 0.9));
        }
    }

    if (clouds) {
        clouds.rotation.y += CLOUD_EXTRA_SPEED * dt;
    }

    // hotspot pulse
    if (layers.hotspots) {

        hotspots.forEach((spot) => {

            const wave = Math.sin(t * 2.2 + spot.phase);
            const boost = spot === selected ? 1.6 : 1;
            const quiet = spot.count === 0;   // loaded and nothing detected in 24 h

            spot.glow.scale.setScalar((quiet ? 0.18 : 0.30 + 0.10 * wave) * boost);
            spot.glow.material.opacity = quiet ? 0.35 : 0.78 + 0.22 * wave;
        });
    }

    if (SATELLITE_SPEED !== 0) {
        satelliteAngle += SATELLITE_SPEED * dt;
        satellite.position.set(
            Math.cos(satelliteAngle) * 2.45,
            Math.sin(satelliteAngle) * 2.45,
            0
        );
    }

    renderer.render(scene, camera);
    starsRenderer.render(starsScene, starsCamera);
}

loadLive();
requestAnimationFrame(animate);

console.log("PLEIADES: Earth system initialized.");