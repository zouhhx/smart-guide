package com.zongheng.exhibitionhallrobot.util;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;

import com.google.zxing.BarcodeFormat;
import com.google.zxing.MultiFormatWriter;
import com.google.zxing.common.BitMatrix;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;

public class ImageUtils {

    public static String imageToBase64(String imagePath) {
        try {
            File file = new File(imagePath);
            FileInputStream fis = new FileInputStream(file);
            ByteArrayOutputStream baos = new ByteArrayOutputStream();

            byte[] buffer = new byte[1024];
            int len;
            while ((len = fis.read(buffer)) != -1) {
                baos.write(buffer, 0, len);
            }

            fis.close();

            byte[] bytes = baos.toByteArray();
            return android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP);

        } catch (Exception e) {
            return null;
        }
    }

    /**
     * Adds a QR code to the bottom-right corner of a photo. The QR content is
     * provided by backend fields such as photoUrl/downloadUrl/qrContent.
     */
    public static boolean addQrCodeToBottomRight(String sourcePath, String targetPath, String qrContent) {
        try {
            Bitmap source = BitmapFactory.decodeFile(sourcePath);
            if (source == null) return false;

            Bitmap result = source.copy(Bitmap.Config.ARGB_8888, true);
            Canvas canvas = new Canvas(result);

            int minSide = Math.min(result.getWidth(), result.getHeight());
            int qrSize = Math.max(160, minSide / 5);
            int margin = Math.max(24, minSide / 40);
            int x = result.getWidth() - qrSize - margin;
            int y = result.getHeight() - qrSize - margin;

            Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
            paint.setStyle(Paint.Style.FILL);
            paint.setColor(Color.WHITE);
            canvas.drawRect(x - 12, y - 12, x + qrSize + 12, y + qrSize + 12, paint);

            Bitmap qrBitmap = createQrBitmap(qrContent, qrSize);
            canvas.drawBitmap(qrBitmap, x, y, null);

            File targetFile = new File(targetPath);
            File parent = targetFile.getParentFile();
            if (parent != null && !parent.exists()) parent.mkdirs();
            FileOutputStream fos = new FileOutputStream(targetFile);
            result.compress(Bitmap.CompressFormat.JPEG, 92, fos);
            fos.flush();
            fos.close();

            qrBitmap.recycle();
            result.recycle();
            source.recycle();
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    private static Bitmap createQrBitmap(String content, int size) throws Exception {
        BitMatrix matrix = new MultiFormatWriter().encode(content, BarcodeFormat.QR_CODE, size, size);
        Bitmap bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
        for (int x = 0; x < size; x++) {
            for (int y = 0; y < size; y++) {
                bitmap.setPixel(x, y, matrix.get(x, y) ? Color.BLACK : Color.WHITE);
            }
        }
        Canvas canvas = new Canvas(bitmap);
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.BLACK);
        paint.setStyle(Paint.Style.STROKE);
        paint.setStrokeWidth(Math.max(2, size / 120));
        canvas.drawRect(new Rect(0, 0, size - 1, size - 1), paint);
        return bitmap;
    }
}
