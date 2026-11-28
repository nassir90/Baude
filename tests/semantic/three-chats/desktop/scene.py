"""The sample scene's desktop code: the lamp drifts and the floor's light follows it. It's given the scene's SceneAPI
and nothing else (scenehost.SceneAPI); it runs only once trusted."""
import math


def setup(scene):
    lamp, light = scene.node("lamp"), scene.lights[0]
    home = list(lamp.position)

    def frame(t, _dt):
        lamp.position = [home[0] + 0.5 * math.sin(t * 0.6), home[1] + 0.15 * math.sin(t * 1.1), home[2]]
        light["position"] = lamp.position

    scene.on_frame(frame)
