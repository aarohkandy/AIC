import { Suspense, useEffect } from 'react'
import { Canvas, useLoader, useThree } from '@react-three/fiber'
import { Environment, OrbitControls } from '@react-three/drei'
import { Box3, Vector3 } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

function FitCamera({ url }: { url: string }) {
  const gltf = useLoader(GLTFLoader, url)
  const { camera } = useThree()

  useEffect(() => {
    const bounds = new Box3().setFromObject(gltf.scene)
    const size = bounds.getSize(new Vector3()).length()
    const center = bounds.getCenter(new Vector3())
    camera.position.set(center.x + size * 0.75, center.y + size * 0.65, center.z + size * 0.85)
    camera.lookAt(center)
    camera.updateProjectionMatrix()
  }, [camera, gltf.scene])

  return <primitive object={gltf.scene} />
}

export function ModelViewer({ url }: { url: string }) {
  return (
    <Canvas camera={{ position: [120, 90, 120], fov: 35 }}>
      <color attach="background" args={['#f6f2e8']} />
      <ambientLight intensity={0.9} />
      <directionalLight position={[6, 12, 8]} intensity={1.4} color="#fff1dc" />
      <directionalLight position={[-8, 4, -6]} intensity={0.55} color="#b7d3dd" />
      <gridHelper args={[220, 22, '#b1a58f', '#e2d7c0']} position={[0, -40, 0]} />
      <Suspense fallback={null}>
        <FitCamera url={url} />
        <Environment preset="studio" />
      </Suspense>
      <OrbitControls enableDamping makeDefault />
    </Canvas>
  )
}
