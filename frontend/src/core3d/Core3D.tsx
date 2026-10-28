/**
 * 3D Evidence Core (lazy-loaded). Five orbital rings, one per pipeline stage,
 * around a nucleus coloured by the run outcome. Every visual property is a pure
 * function of CoreState — the scene never animates progress the backend has not
 * reported. It animates only while a run is live, and pauses while the tab is hidden.
 */
import { Canvas, useFrame } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Group, Mesh } from "three";
import { STAGES, type CoreState, type StageState } from "../lib/pipeline";

const HEX: Record<StageState, string> = {
  idle: "#3a4a66",
  active: "#42e8f5",
  done: "#5488ff",
  skipped: "#26324a",
};
const OUTCOME_HEX: Record<string, string> = {
  none: "#5d6b85",
  confirmed: "#ff4567",
  inconclusive: "#ffbf69",
  clear: "#5fd4a6",
  failed: "#ffbf69",
};
const TILTS: [number, number][] = [
  [1.25, 0.1],
  [0.6, 0.9],
  [1.9, -0.5],
  [0.25, -1.1],
  [1.0, 1.6],
];

function Ring({ index, state, live }: { index: number; state: StageState; live: boolean }) {
  const group = useRef<Group>(null);
  const bead = useRef<Mesh>(null);
  const radius = 2.15 - index * 0.32;
  const [tx, tz] = TILTS[index] ?? [0, 0];
  const speed = state === "active" ? 0.9 : state === "done" ? 0.12 : 0.04;
  useFrame((_, dt) => {
    if (!group.current) return;
    group.current.rotation.z += dt * speed * (index % 2 ? -1 : 1);
    if (bead.current) bead.current.visible = state === "active" && live;
  });
  const opacity = state === "skipped" ? 0.25 : state === "idle" ? 0.45 : 1;
  return (
    <group rotation={[tx, 0, tz]}>
      <group ref={group}>
        <mesh>
          <torusGeometry args={[radius, state === "active" ? 0.028 : 0.014, 12, 160]} />
          <meshStandardMaterial
            color={HEX[state]}
            emissive={HEX[state]}
            emissiveIntensity={state === "active" ? 1.6 : state === "done" ? 0.5 : 0.15}
            transparent
            opacity={opacity}
          />
        </mesh>
        <mesh ref={bead} position={[radius, 0, 0]}>
          <sphereGeometry args={[0.06, 16, 16]} />
          <meshBasicMaterial color="#e9fdff" />
        </mesh>
      </group>
    </group>
  );
}

function Nucleus({ outcome, live }: { outcome: string; live: boolean }) {
  const ref = useRef<Mesh>(null);
  useFrame(({ clock }, dt) => {
    if (!ref.current) return;
    ref.current.rotation.y += dt * (live ? 0.6 : 0.15);
    const s = live ? 1 + Math.sin(clock.elapsedTime * 3) * 0.04 : 1;
    ref.current.scale.setScalar(s);
  });
  const color = OUTCOME_HEX[outcome] ?? OUTCOME_HEX.none;
  return (
    <group>
      <mesh ref={ref}>
        <icosahedronGeometry args={[0.42, 1]} />
        <meshStandardMaterial color={color} emissive={color} emissiveIntensity={0.9} flatShading />
      </mesh>
      <mesh>
        <sphereGeometry args={[0.62, 32, 32]} />
        <meshBasicMaterial color={color} transparent opacity={0.08} />
      </mesh>
    </group>
  );
}

function usePageVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState === "visible");
  useEffect(() => {
    const on = () => setVisible(document.visibilityState === "visible");
    document.addEventListener("visibilitychange", on);
    return () => document.removeEventListener("visibilitychange", on);
  }, []);
  return visible;
}

export default function Core3D({ state }: { state: CoreState }) {
  const visible = usePageVisible();
  const rings = useMemo(() => STAGES.map((s, i) => ({ s, i })), []);
  return (
    <Canvas
      frameloop={!visible ? "never" : state.live ? "always" : "demand"}
      dpr={[1, 1.75]}
      camera={{ position: [0, 0, 6.2], fov: 45 }}
      gl={{ antialias: true, alpha: true, powerPreference: "low-power" }}
      aria-hidden
    >
      <ambientLight intensity={0.35} />
      <pointLight position={[4, 4, 5]} intensity={30} color="#9ecbff" />
      <pointLight position={[-4, -3, 2]} intensity={12} color="#a78bfa" />
      <Nucleus outcome={state.outcome} live={state.live} />
      {rings.map(({ s, i }) => (
        <Ring key={s} index={i} state={state.stages[s]} live={state.live} />
      ))}
    </Canvas>
  );
}
