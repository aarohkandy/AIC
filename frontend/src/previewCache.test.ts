import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  BoxGeometry,
  Group,
  Mesh,
  MeshStandardMaterial,
  Points,
  PointsMaterial,
  Scene,
  Texture,
} from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { useLoader } from '@react-three/fiber'
import { releasePreview, trackPreviewScene } from './previewCache'

// releasePreview only wants useLoader.clear, and the real module brings the
// whole renderer with it. vi.mock is hoisted above the imports, so this has to
// stay at the top level of the file.
vi.mock('@react-three/fiber', () => ({ useLoader: { clear: vi.fn() } }))

// App gives each build its own artifact URL so a revision is never served the
// previous geometry, and the cache is keyed on that URL. One per test keeps
// them off each other's key.
let previewUrl = ''
let build = 0

beforeEach(() => {
  build += 1
  previewUrl = `http://backend.test/designs/d1/artifacts/glb?build=${build}`
  vi.mocked(useLoader.clear).mockClear()
})

describe('releasePreview', () => {
  it('drops the loader entry and disposes what the mesh held', () => {
    const geometry = new BoxGeometry()
    const material = new MeshStandardMaterial()
    const scene = new Scene()
    scene.add(new Mesh(geometry, material))
    const disposals = [vi.spyOn(geometry, 'dispose'), vi.spyOn(material, 'dispose')]

    trackPreviewScene(previewUrl, scene)
    releasePreview(previewUrl)

    expect(vi.mocked(useLoader.clear)).toHaveBeenCalledWith(GLTFLoader, previewUrl)
    disposals.forEach((disposal) => expect(disposal).toHaveBeenCalledTimes(1))
  })

  it('disposes every material in an array and the textures they carry', () => {
    const map = new Texture()
    const normalMap = new Texture()
    const outside = new MeshStandardMaterial({ map })
    const inside = new MeshStandardMaterial({ normalMap })
    const scene = new Scene()
    scene.add(new Mesh(new BoxGeometry(), [outside, inside]))
    const disposals = [map, normalMap, outside, inside].map((held) => vi.spyOn(held, 'dispose'))

    trackPreviewScene(previewUrl, scene)
    releasePreview(previewUrl)

    disposals.forEach((disposal) => expect(disposal).toHaveBeenCalledTimes(1))
  })

  it('reaches a mesh nested under a group, which is how a GLB arrives', () => {
    const geometry = new BoxGeometry()
    const group = new Group()
    group.add(new Mesh(geometry, new MeshStandardMaterial()))
    const scene = new Scene()
    scene.add(group)
    const disposal = vi.spyOn(geometry, 'dispose')

    trackPreviewScene(previewUrl, scene)
    releasePreview(previewUrl)

    expect(disposal).toHaveBeenCalledTimes(1)
  })

  it('has nothing left to free the second time the same preview is released', () => {
    const geometry = new BoxGeometry()
    const scene = new Scene()
    scene.add(new Mesh(geometry, new MeshStandardMaterial()))
    const disposal = vi.spyOn(geometry, 'dispose')

    trackPreviewScene(previewUrl, scene)
    releasePreview(previewUrl)
    releasePreview(previewUrl)

    expect(disposal).toHaveBeenCalledTimes(1)
  })

  it('walks past a point cloud, which is the documented limit', () => {
    // The walk descends into Mesh and nothing else on purpose. Widening it to
    // anything carrying a .geometry would also catch Sprite, and every sprite
    // on the page shares one module-level geometry, so that trades a gap this
    // app cannot reach for one it can. Its GLBs come out of a CadQuery
    // Assembly export and are solid meshes.
    const geometry = new BoxGeometry()
    const scene = new Scene()
    scene.add(new Points(geometry, new PointsMaterial()))
    const disposal = vi.spyOn(geometry, 'dispose')

    trackPreviewScene(previewUrl, scene)
    releasePreview(previewUrl)

    expect(disposal).not.toHaveBeenCalled()
  })
})
