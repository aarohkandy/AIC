import { useLoader } from '@react-three/fiber'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

/** Drop a GLB from the loader cache once nothing is rendering it.
 *
 * useLoader keeps every (loader, url) pair in suspend-react's module-global
 * cache with no lifespan. Each build gets its own URL so a revision is not
 * served the previous geometry, which means without this the page holds one
 * fully parsed scene per build for as long as it stays open.
 */
export function releasePreview(url: string): void {
  useLoader.clear(GLTFLoader, url)
}
