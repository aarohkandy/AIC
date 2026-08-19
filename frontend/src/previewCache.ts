import { useLoader } from '@react-three/fiber'
import { Mesh, Texture, type Material, type Object3D } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

const parsedScenes = new Map<string, Object3D>()

/** Record what a preview URL parsed into so releasePreview can free it later.
 *
 * The viewer is the only thing holding the scene, and App is the only thing that
 * knows when a preview has been replaced, so the two meet here.
 */
export function trackPreviewScene(url: string, scene: Object3D): void {
  parsedScenes.set(url, scene)
}

/** Drop a GLB from the loader cache and off the GPU once nothing is rendering it.
 *
 * useLoader keeps every (loader, url) pair in suspend-react's module-global cache
 * with no lifespan. Each build gets its own URL so a revision is not served the
 * previous geometry, which means without this the page holds one fully parsed
 * scene per build for as long as it stays open. Clearing that entry is only half
 * of it: three.js frees nothing on its own, and R3F does not dispose an object it
 * did not create, so the geometries, materials and textures behind <primitive>
 * stay resident until they are disposed by hand.
 */
export function releasePreview(url: string): void {
  useLoader.clear(GLTFLoader, url)

  const scene = parsedScenes.get(url)
  if (!scene) {
    return
  }
  parsedScenes.delete(url)
  scene.traverse((object) => {
    if (!(object instanceof Mesh)) {
      return
    }
    object.geometry.dispose()
    const materials: Material[] = Array.isArray(object.material) ? object.material : [object.material]
    materials.forEach(disposeMaterial)
  })
}

function disposeMaterial(material: Material): void {
  // Which slots hold a texture depends on the material, and a GLB can bring any of
  // them, so ask the instance rather than listing map names.
  for (const value of Object.values(material as unknown as Record<string, unknown>)) {
    if (value instanceof Texture) {
      value.dispose()
    }
  }
  material.dispose()
}
