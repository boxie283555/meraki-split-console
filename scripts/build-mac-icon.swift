// Original network/cloud symbol for this independent Meraki management console.
import AppKit
let out = CommandLine.arguments[1]
let size = 1024
let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
let bg = NSBezierPath(roundedRect: NSRect(x: 48, y: 48, width: 928, height: 928), xRadius: 210, yRadius: 210)
NSGradient(starting: NSColor(calibratedRed: 0.31, green: 0.77, blue: 0.27, alpha: 1), ending: NSColor(calibratedRed: 0.02, green: 0.37, blue: 0.24, alpha: 1))!.draw(in: bg, angle: -90)
NSColor.white.setFill()
// Cloud silhouette.
let cloud = NSBezierPath(roundedRect: NSRect(x: 225, y: 490, width: 574, height: 145), xRadius: 72, yRadius: 72)
cloud.appendOval(in: NSRect(x: 296, y: 539, width: 230, height: 230))
cloud.appendOval(in: NSRect(x: 455, y: 558, width: 240, height: 240))
cloud.appendOval(in: NSRect(x: 620, y: 521, width: 154, height: 154))
cloud.fill()
let links = NSBezierPath()
links.move(to: NSPoint(x:512,y:495));links.line(to:NSPoint(x:512,y:395))
links.move(to:NSPoint(x:280,y:310));links.line(to:NSPoint(x:280,y:395));links.line(to:NSPoint(x:744,y:395));links.line(to:NSPoint(x:744,y:310))
links.move(to:NSPoint(x:512,y:395));links.line(to:NSPoint(x:512,y:310))
links.lineWidth=25;links.lineCapStyle = .round;links.lineJoinStyle = .round
NSColor.white.withAlphaComponent(0.9).setStroke();links.stroke()
for x in [280,512,744] {
 let node=NSBezierPath(roundedRect:NSRect(x:x-51,y:220,width:102,height:88),xRadius:22,yRadius:22)
 NSColor.white.setFill();node.fill()
}
image.unlockFocus()
let bitmap=NSBitmapImageRep(data:image.tiffRepresentation!)!
try bitmap.representation(using:.png,properties:[:])!.write(to:URL(fileURLWithPath:out))
