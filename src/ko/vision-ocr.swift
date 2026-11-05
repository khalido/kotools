// Apple Vision OCR for `ko ocr`: free, on-device. Prints liteparse's OCR-server JSON for one image:
//   {"results":[{"text","bbox":[x1,y1,x2,y2],"confidence"}]}  pixels, origin top-left, reading order.
// Usage: vision-ocr <png|jpg|->   ("-" reads stdin). ko builds it on first use (ocr.py `_vision_bin`).
// Source: ~/code/pk_legal/libs/src/vision-ocr.swift, `lines` mode only (its `doc` mode drops the ends of
// tightly set lines), changed to print boxes and confidence instead of plain lines.
import CoreGraphics
import Foundation
import ImageIO
import Vision

let args = CommandLine.arguments
guard args.count == 2 else {
  FileHandle.standardError.write("usage: vision-ocr <png|jpg|->\n".data(using: .utf8)!)
  exit(2)
}
let image: Data = args[1] == "-" ? FileHandle.standardInput.readDataToEndOfFile() : try! Data(contentsOf: URL(fileURLWithPath: args[1]))

do {
  guard let src = CGImageSourceCreateWithData(image as CFData, nil), let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
    throw NSError(domain: "vision-ocr", code: 1, userInfo: [NSLocalizedDescriptionKey: "not an image"])
  }
  let w = Double(cg.width), h = Double(cg.height)
  let req = VNRecognizeTextRequest()
  req.recognitionLevel = .accurate
  req.recognitionLanguages = ["en-US"]
  req.usesLanguageCorrection = true
  try VNImageRequestHandler(cgImage: cg).perform([req])
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
