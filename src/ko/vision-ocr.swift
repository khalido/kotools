// Replaced once liteparse ships native Vision: https://github.com/run-llama/liteparse/pull/521
// Apple Vision OCR for `ko ocr`: free, on-device. Prints liteparse's OCR-server JSON for one image:
//   {"results":[{"text","bbox":[x1,y1,x2,y2],"confidence"}]}  pixels, origin top-left, reading order.
// Usage: vision-ocr <png|jpg|-|--warm>   ("-" reads stdin). ko builds it on first use (ocr.py `_vision_bin`).
// Source: ~/code/pk_legal/libs/src/vision-ocr.swift, `lines` mode only (its `doc` mode drops the ends of
// tightly set lines), changed to print boxes and confidence instead of plain lines.
import CoreGraphics
import CoreText
import Foundation
import ImageIO
import Vision

let args = CommandLine.arguments
guard args.count == 2 else {
  FileHandle.standardError.write("usage: vision-ocr <png|jpg|-|--warm>\n".data(using: .utf8)!)
  exit(2)
}

/// A page of text to read once after a build: macOS loads the recognizer on a binary's first real
/// read (12-58 s), and a blank or tiny image doesn't get that far.
func warmImage() -> CGImage? {
  guard let ctx = CGContext(data: nil, width: 800, height: 120, bitsPerComponent: 8, bytesPerRow: 0, space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue) else { return nil }
  ctx.setFillColor(gray: 1, alpha: 1)
  ctx.fill(CGRect(x: 0, y: 0, width: 800, height: 120))
  let attrs: [NSAttributedString.Key: Any] = [.init(kCTFontAttributeName as String): CTFontCreateWithName("Helvetica" as CFString, 40, nil), .init(kCTForegroundColorAttributeName as String): CGColor(gray: 0, alpha: 1)]
  ctx.textPosition = CGPoint(x: 40, y: 45)
  CTLineDraw(CTLineCreateWithAttributedString(NSAttributedString(string: "warming up the recognizer", attributes: attrs)), ctx)
  return ctx.makeImage()
}

func decoded() -> CGImage? {
  let image: Data = args[1] == "-" ? FileHandle.standardInput.readDataToEndOfFile() : try! Data(contentsOf: URL(fileURLWithPath: args[1]))
  guard let src = CGImageSourceCreateWithData(image as CFData, nil) else { return nil }
  return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

do {
  guard let cg = args[1] == "--warm" ? warmImage() : decoded() else {
    throw NSError(domain: "vision-ocr", code: 1, userInfo: [NSLocalizedDescriptionKey: "not an image"])
  }
  let w = Double(cg.width), h = Double(cg.height)
  let req = VNRecognizeTextRequest()
  req.recognitionLevel = .accurate
  req.recognitionLanguages = ["en-US"]
  req.usesLanguageCorrection = true
  // Vision refuses an image under 3 px a side; nothing that small holds text to read.
  if cg.width >= 3 && cg.height >= 3 { try VNImageRequestHandler(cgImage: cg).perform([req]) }
  // Vision's box is normalised with the origin bottom-left: sort by top edge descending, then left edge.
  let obs = (req.results ?? []).sorted {
    abs($0.boundingBox.maxY - $1.boundingBox.maxY) > 0.005 ? $0.boundingBox.maxY > $1.boundingBox.maxY : $0.boundingBox.minX < $1.boundingBox.minX
  }
  let results: [[String: Any]] = obs.compactMap { o in
    guard let c = o.topCandidates(1).first else { return nil }
    let b = o.boundingBox
    let box = [b.minX * w, (1 - b.maxY) * h, b.maxX * w, (1 - b.minY) * h].map { ($0 * 10).rounded() / 10 }
    return ["text": c.string, "bbox": box, "confidence": Double(c.confidence)]
  }
  let out = try JSONSerialization.data(withJSONObject: ["results": results])
  FileHandle.standardOutput.write(out)
} catch {
  FileHandle.standardError.write("vision-ocr: \(error)\n".data(using: .utf8)!)
  exit(1)
}
