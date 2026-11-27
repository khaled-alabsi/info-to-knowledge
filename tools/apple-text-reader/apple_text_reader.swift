// Reads the words of one image with Apple's text recognizer (Vision, accurate level, English and German) and prints
// them as JSON: [{"text", "box": [left, top, right, bottom] in pixels from the top left, "line": n, "confidence": c}],
// one line number per recognised line, and the recogniser's confidence in that line, 0 to 1. Built by services/apple_text_service.py on first use. Usage: apple-text-reader <image>
import Foundation
import Vision
import ImageIO

let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let source = CGImageSourceCreateWithURL(url as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(source, 0, nil) else {
    FileHandle.standardError.write("cannot read \(url.path)\n".data(using: .utf8)!)
    exit(1)
}
let width = Double(image.width), height = Double(image.height)
let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.recognitionLanguages = ["en-US", "de-DE"]
request.usesLanguageCorrection = false
try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])

var words: [[String: Any]] = []
for (line, observation) in (request.results ?? []).enumerated() {
    guard let candidate = observation.topCandidates(1).first else { continue }
    let text = candidate.string
    var index = text.startIndex
    for part in text.split(separator: " ", omittingEmptySubsequences: true) {
        let range = text.range(of: part, range: index..<text.endIndex)!
        index = range.upperBound
        guard let box = try? candidate.boundingBox(for: range)?.boundingBox else { continue }
        words.append(["text": String(part), "line": line, "confidence": Double(candidate.confidence),
                      "box": [Int(box.minX * width), Int((1 - box.maxY) * height),
                              Int(box.maxX * width), Int((1 - box.minY) * height)]])
    }
}
let data = try JSONSerialization.data(withJSONObject: words)
FileHandle.standardOutput.write(data)
