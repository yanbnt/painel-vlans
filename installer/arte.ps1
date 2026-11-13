# Desenha o ícone e as imagens do instalador do Painel de VLANs, sem nenhum programa extra:
# usa o desenho do próprio Windows (System.Drawing). Chamado por gerar-instalador.ps1.
#
#   painel.ico          ícone do programa, do atalho e do instalador (16 a 256 pixels)
#   pequena-NN.png      ícone do canto superior do assistente, em 4 tamanhos de tela
#   lateral-NNN.png     imagem lateral das telas de boas-vindas e de conclusão, em 6 tamanhos de tela
#
# O ícone é um switch com as portas acesas nas cores das VLANs, as mesmas do painel.

Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Drawing.Text;
using System.IO;
using System.Runtime.InteropServices;

public static class ArtePainel
{
    // The panel's own palette: slate chassis, VLAN colours for everything that is lit.
    static readonly string Blue = "#3987E5", Orange = "#D95926", Green = "#199E70", Violet = "#9085E9", Amber = "#C98500", Unlit = "#2C3845";

    static Color C(string hex) { return ColorTranslator.FromHtml(hex); }
    static Color A(int alpha, string hex) { return Color.FromArgb(alpha, ColorTranslator.FromHtml(hex)); }

    static GraphicsPath Round(float x, float y, float w, float h, float r)
    {
        GraphicsPath p = new GraphicsPath();
        float d = Math.Min(r * 2, Math.Min(w, h));
        if (d < 0.5f) { p.AddRectangle(new RectangleF(x, y, w, h)); return p; }
        p.AddArc(x, y, d, d, 180, 90);
        p.AddArc(x + w - d, y, d, d, 270, 90);
        p.AddArc(x + w - d, y + h - d, d, d, 0, 90);
        p.AddArc(x, y + h - d, d, d, 90, 90);
        p.CloseFigure();
        return p;
    }

    static void Fill(Graphics g, string hex, float x, float y, float w, float h, float r)
    {
        using (GraphicsPath p = Round(x, y, w, h, r))
        using (SolidBrush b = new SolidBrush(C(hex)))
            g.FillPath(b, p);
    }

    static Graphics Prep(Bitmap b)
    {
        Graphics g = Graphics.FromImage(b);
        g.SmoothingMode = SmoothingMode.AntiAlias;
        g.PixelOffsetMode = PixelOffsetMode.HighQuality;
        g.InterpolationMode = InterpolationMode.HighQualityBicubic;
        g.CompositingQuality = CompositingQuality.HighQuality;
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit;
        return g;
    }

    // ---- the icon

    static void Icon(Graphics g, float ox, float oy, float s)
    {
        bool tiny = s < 48;
        float m = tiny ? Math.Max(1f, (float)Math.Round(s * 0.04)) : s * 0.055f;
        float side = s - 2 * m;
        using (GraphicsPath tile = Round(ox + m, oy + m, side, side, s * 0.22f))
        {
            using (LinearGradientBrush b = new LinearGradientBrush(new PointF(0, oy + m), new PointF(0, oy + m + side), C("#3B4B5D"), C("#151C24")))
                g.FillPath(b, tile);
            if (!tiny)
                using (Pen edge = new Pen(A(40, "#FFFFFF"), Math.Max(1f, s / 160f)))
                    g.DrawPath(edge, tile);
        }
        if (tiny)
        {
            // Few pixels: a crisp grid of lit ports, nothing else.
            string[] colours = s < 24 ? new string[] { Blue, Orange, Green, Violet } : new string[] { Blue, Blue, Orange, Green, Violet, Unlit };
            int cols = s < 24 ? 2 : 3;
            int port = (int)Math.Round(s < 24 ? s * 0.26 : s * 0.19), gap = Math.Max(1, (int)Math.Round(s * 0.07));
            int gw = cols * port + (cols - 1) * gap, gh = 2 * port + gap;
            float x0 = ox + (float)Math.Floor((s - gw) / 2.0), y0 = oy + (float)Math.Floor((s - gh) / 2.0);
            SmoothingMode keep = g.SmoothingMode;
            g.SmoothingMode = SmoothingMode.None;
            for (int i = 0; i < colours.Length; i++)
                using (SolidBrush b = new SolidBrush(C(colours[i])))
                    g.FillRectangle(b, x0 + (i % cols) * (port + gap), y0 + (i / cols) * (port + gap), port, port);
            g.SmoothingMode = keep;
            return;
        }
        // The label strip and the link light of a switch front...
        Fill(g, "#DCE3EA", ox + s * 0.19f, oy + s * 0.235f, s * 0.30f, s * 0.06f, s * 0.03f);
        using (SolidBrush led = new SolidBrush(C("#2BD47D")))
            g.FillEllipse(led, ox + s * 0.735f, oy + s * 0.23f, s * 0.07f, s * 0.07f);
        // ...and its ports, lit in the colour of their VLAN.
        Fill(g, "#0E1419", ox + s * 0.15f, oy + s * 0.385f, s * 0.70f, s * 0.385f, s * 0.07f);
        string[] lit = { Blue, Blue, Orange, Orange, Green, Green, Violet, Unlit };
        float p = s * 0.115f, gp = s * 0.038f;
        float gx = ox + s * 0.5f - (4 * p + 3 * gp) / 2, gy = oy + s * 0.385f + (s * 0.385f - (2 * p + gp)) / 2;
        for (int i = 0; i < 8; i++)
            Fill(g, lit[i], gx + (i % 4) * (p + gp), gy + (i / 4) * (p + gp), p, p, p * 0.18f);
    }

    public static Bitmap Icone(int s)
    {
        Bitmap b = new Bitmap(s, s, PixelFormat.Format32bppArgb);
        using (Graphics g = Prep(b))
        {
            g.Clear(Color.Transparent);
            Icon(g, 0, 0, s);
        }
        return b;
    }

    // ---- the side picture of the welcome and finish pages (drawn on a 430 x 824 grid, scaled)

    public static Bitmap Lateral(int w, int h, string versao)
    {
        Bitmap b = new Bitmap(w, h, PixelFormat.Format24bppRgb);
        float k = w / 430f;
        using (Graphics g = Prep(b))
        {
            using (LinearGradientBrush bg = new LinearGradientBrush(new PointF(0, 0), new PointF(0, h), C("#0B151F"), C("#163049")))
                g.FillRectangle(bg, 0, 0, w, h);
            using (GraphicsPath glow = new GraphicsPath())
            {
                glow.AddEllipse(215 * k - 250 * k, 185 * k - 210 * k, 500 * k, 420 * k);
                using (PathGradientBrush pg = new PathGradientBrush(glow))
                {
                    pg.CenterColor = A(120, "#2F6DB2");
                    pg.SurroundColors = new Color[] { A(0, "#2F6DB2") };
                    g.FillPath(pg, glow);
                }
            }

            Icon(g, 215 * k - 74 * k, 96 * k, 148 * k);

            using (StringFormat center = new StringFormat())
            using (SolidBrush soft = new SolidBrush(C("#9DB2C7")))
            using (SolidBrush white = new SolidBrush(Color.White))
            using (SolidBrush note = new SolidBrush(C("#B4C5D6")))
            using (Font f1 = new Font("Segoe UI", 25 * k, FontStyle.Regular, GraphicsUnit.Pixel))
            using (Font f2 = new Font("Segoe UI Semibold", 64 * k, FontStyle.Regular, GraphicsUnit.Pixel))
            using (Font f3 = new Font("Segoe UI", 19 * k, FontStyle.Regular, GraphicsUnit.Pixel))
            {
                center.Alignment = StringAlignment.Center;
                g.DrawString("Painel de", f1, soft, new RectangleF(0, 268 * k, w, 40 * k), center);
                g.DrawString("VLANs", f2, white, new RectangleF(0, 292 * k, w, 90 * k), center);
                string[] bar = { Blue, Orange, Green };
                for (int i = 0; i < 3; i++)
                    Fill(g, bar[i], 215 * k - 41 * k + i * 28 * k, 392 * k, 22 * k, 5 * k, 2.5f * k);
                g.DrawString("Equipamentos da rede,\nVLANs e switches", f3, note, new RectangleF(0, 414 * k, w, 60 * k), center);
            }

            // Devices of each VLAN, wired into the ports of a switch.
            float px0 = 45 * k, pw = 340 * k, py0 = 688 * k, ph = 46 * k;
            float port = 18 * k, gap = 7 * k;
            float gx = px0 + (pw - (12 * port + 11 * gap)) / 2, gy = py0 + (ph - port) / 2;
            string[] portCol = { Blue, Blue, Blue, Blue, Orange, Orange, Orange, Green, Green, Violet, Amber, Unlit };
            float[] nodeX = { 58, 96, 134, 170, 204, 238, 270, 302, 334, 364, 392 };
            float[] nodeY = { 562, 518, 592, 540, 604, 530, 586, 546, 602, 522, 578 };
            for (int i = 0; i < 11; i++)
            {
                float cx = gx + i * (port + gap) + port / 2, cy = gy;
                float nx = nodeX[i] * k, ny = nodeY[i] * k;
                using (Pen cable = new Pen(A(150, portCol[i]), 1.6f * k))
                    g.DrawBezier(cable, nx, ny, nx, ny + 55 * k, cx, cy - 55 * k, cx, cy);
                using (SolidBrush halo = new SolidBrush(A(60, portCol[i])))
                    g.FillEllipse(halo, nx - 11 * k, ny - 11 * k, 22 * k, 22 * k);
                using (SolidBrush dot = new SolidBrush(C(portCol[i])))
                    g.FillEllipse(dot, nx - 6 * k, ny - 6 * k, 12 * k, 12 * k);
            }
            using (GraphicsPath face = Round(px0, py0, pw, ph, 8 * k))
            using (SolidBrush fb = new SolidBrush(C("#0A1016")))
            using (Pen fp = new Pen(C("#2A3541"), 1.5f * k))
            {
                g.FillPath(fb, face);
                g.DrawPath(fp, face);
            }
            for (int i = 0; i < 12; i++)
                Fill(g, portCol[i], gx + i * (port + gap), gy, port, port, 3 * k);

            using (StringFormat center = new StringFormat())
            using (SolidBrush dim = new SolidBrush(C("#6E8397")))
            using (Font f = new Font("Segoe UI", 15 * k, FontStyle.Regular, GraphicsUnit.Pixel))
            {
                center.Alignment = StringAlignment.Center;
                g.DrawString("versão " + versao, f, dim, new RectangleF(0, 772 * k, w, 30 * k), center);
            }
        }
        return b;
    }

    // ---- files

    static byte[] Png(Bitmap b)
    {
        using (MemoryStream ms = new MemoryStream())
        {
            b.Save(ms, ImageFormat.Png);
            return ms.ToArray();
        }
    }

    // A classic icon image (32-bit colour with alpha, plus the empty mask Windows expects).
    static byte[] Dib(Bitmap b)
    {
        int w = b.Width, h = b.Height;
        BitmapData d = b.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
        int stride = d.Stride;
        byte[] px = new byte[stride * h];
        Marshal.Copy(d.Scan0, px, 0, px.Length);
        b.UnlockBits(d);
        int maskStride = ((w + 31) / 32) * 4;
        using (MemoryStream ms = new MemoryStream())
        using (BinaryWriter bw = new BinaryWriter(ms))
        {
            bw.Write(40); bw.Write(w); bw.Write(h * 2); bw.Write((short)1); bw.Write((short)32);
            bw.Write(0); bw.Write(w * h * 4 + maskStride * h); bw.Write(0); bw.Write(0); bw.Write(0); bw.Write(0);
            for (int y = h - 1; y >= 0; y--)
                bw.Write(px, y * stride, w * 4);
            bw.Write(new byte[maskStride * h]);
            bw.Flush();
            return ms.ToArray();
        }
    }

    static void SaveIco(string path, int[] sizes)
    {
        List<byte[]> images = new List<byte[]>();
        foreach (int s in sizes)
            using (Bitmap b = Icone(s))
                images.Add(s >= 128 ? Png(b) : Dib(b));
        using (FileStream fs = File.Create(path))
        using (BinaryWriter bw = new BinaryWriter(fs))
        {
            bw.Write((short)0); bw.Write((short)1); bw.Write((short)sizes.Length);
            int offset = 6 + 16 * sizes.Length;
            for (int i = 0; i < sizes.Length; i++)
            {
                byte side = (byte)(sizes[i] >= 256 ? 0 : sizes[i]);
                bw.Write(side); bw.Write(side); bw.Write((byte)0); bw.Write((byte)0);
                bw.Write((short)1); bw.Write((short)32); bw.Write(images[i].Length); bw.Write(offset);
                offset += images[i].Length;
            }
            foreach (byte[] img in images)
                bw.Write(img);
        }
    }

    public static void Gerar(string pasta, string versao)
    {
        Directory.CreateDirectory(pasta);
        SaveIco(Path.Combine(pasta, "painel.ico"), new int[] { 16, 20, 24, 32, 40, 48, 64, 128, 256 });
        foreach (int s in new int[] { 58, 77, 97, 124 })
            using (Bitmap b = Icone(s))
                b.Save(Path.Combine(pasta, "pequena-" + s + ".png"), ImageFormat.Png);
        int[,] sides = { { 202, 386 }, { 269, 515 }, { 336, 643 }, { 403, 772 }, { 430, 824 }, { 498, 953 } };
        for (int i = 0; i < sides.GetLength(0); i++)
            using (Bitmap b = Lateral(sides[i, 0], sides[i, 1], versao))
                b.Save(Path.Combine(pasta, "lateral-" + sides[i, 0] + ".png"), ImageFormat.Png);
        using (Bitmap b = Icone(256))
            b.Save(Path.Combine(pasta, "icone-256.png"), ImageFormat.Png);
    }
}
'@
