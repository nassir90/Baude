package dev.baude.android

import androidx.compose.runtime.Composable

/** Kubernetes (kubeview.KubeView): a cluster's objects as Freelens lists them, read through the host. */
@Composable
fun KubeScreen(hostId: String, back: () -> Unit, context: String? = null, page: String? = null, search: String? = null,
               namespace: String? = null) {
    Bar(Wording.Window.kubernetes, start = { IconButton(Adwaita.GoPrevious, Wording.Browser.back, onClick = back) })
}
