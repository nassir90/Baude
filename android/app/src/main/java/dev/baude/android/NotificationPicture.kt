package dev.baude.android

import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapShader
import android.graphics.Canvas
import android.graphics.Matrix
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.Shader
import androidx.core.content.ContextCompat

/** A Bot's picture as its notifications show it (ChatNotification, as the desktop's widgets.notification_picture):
 * round, Baude's logo in its bottom-right corner, cut out of it. */
fun notificationPicture(context: Context, picture: Bitmap): Bitmap {
    val size = Tokens.Notification.picture.value.toInt()
    val badge = Tokens.Notification.badge.value.toInt()
    val gap = Tokens.Notification.badgeGap.value
    val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
    val canvas = Canvas(out)
    val k = size.toFloat() / minOf(picture.width, picture.height) // (filling the circle, cropped to its middle)
    val shader = BitmapShader(picture, Shader.TileMode.CLAMP, Shader.TileMode.CLAMP).apply {
        setLocalMatrix(Matrix().apply {
            setScale(k, k)
            postTranslate((size - picture.width * k) / 2, (size - picture.height * k) / 2)
        })
    }
    canvas.drawCircle(size / 2f, size / 2f, size / 2f, Paint(Paint.ANTI_ALIAS_FLAG or Paint.FILTER_BITMAP_FLAG).apply { this.shader = shader })
    val at = size - badge
    canvas.drawCircle(at + badge / 2f, at + badge / 2f, badge / 2f + gap, // (a gap round the logo, whatever's behind it)
        Paint(Paint.ANTI_ALIAS_FLAG).apply { xfermode = PorterDuffXfermode(PorterDuff.Mode.CLEAR) })
    ContextCompat.getDrawable(context, R.mipmap.ic_launcher)?.apply {
        setBounds(at, at, size, size)
        draw(canvas)
    }
    return out
}
