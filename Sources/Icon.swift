import AppKit

let output = CommandLine.arguments[1]
let folder = URL(fileURLWithPath: output)
try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let image = NSImage(size: NSSize(width: pixels, height: pixels))
        image.lockFocus()
        let p = CGFloat(pixels)
        let rect = NSRect(x: p*0.05, y: p*0.05, width: p*0.9, height: p*0.9)
        let shape = NSBezierPath(roundedRect: rect, xRadius: p*0.2, yRadius: p*0.2)
        let gradient = NSGradient(starting: NSColor(calibratedRed: 0.18, green: 0.54, blue: 0.96, alpha: 1),
                                  ending: NSColor(calibratedRed: 0.12, green: 0.20, blue: 0.55, alpha: 1))!
        gradient.draw(in: shape, angle: -90)
        if let symbol = NSImage(systemSymbolName: "iphone.and.arrow.forward", accessibilityDescription: nil)?
            .withSymbolConfiguration(.init(pointSize: p*0.52, weight: .medium)) {
            let tinted = NSImage(size: symbol.size)
            tinted.lockFocus()
            symbol.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)
            NSColor.white.setFill()
            NSRect(origin: .zero, size: symbol.size).fill(using: .sourceAtop)
            tinted.unlockFocus()
            let ratio = min(p*0.58/symbol.size.width, p*0.64/symbol.size.height)
            let s = NSSize(width: symbol.size.width*ratio, height: symbol.size.height*ratio)
            tinted.draw(in: NSRect(x: (p-s.width)/2, y: (p-s.height)/2, width: s.width, height: s.height))
        }
        image.unlockFocus()
        let bitmap = NSBitmapImageRep(data: image.tiffRepresentation!)!
        let name = "icon_\(size)x\(size)" + (scale == 2 ? "@2x" : "") + ".png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: folder.appendingPathComponent(name))
    }
}
