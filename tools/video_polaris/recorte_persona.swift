// Recorta la silueta de una persona (fondo transparente) con Vision de macOS, en local, sin nada de terceros.
// Uso: swift recorte_persona.swift <entrada.jpg> <salida.png>
// Para el vídeo «Polaris»: su foto de prensa pública (fondo blanco de estudio) sobre el fondo oscuro de la marca (diseño, 28-sep).
import Foundation
import Vision
import CoreImage
import CoreImage.CIFilterBuiltins
import AppKit

let args = CommandLine.arguments
guard args.count == 3, let img = CIImage(contentsOf: URL(fileURLWithPath: args[1])) else { print("uso: entrada salida"); exit(1) }
let req = VNGeneratePersonSegmentationRequest()
req.qualityLevel = .accurate
req.outputPixelFormat = kCVPixelFormatType_OneComponent8
try VNImageRequestHandler(ciImage: img).perform([req])
guard let buf = req.results?.first?.pixelBuffer else { print("sin persona"); exit(2) }
var mask = CIImage(cvPixelBuffer: buf)
mask = mask.transformed(by: CGAffineTransform(scaleX: img.extent.width / mask.extent.width, y: img.extent.height / mask.extent.height))
let blend = CIFilter.blendWithMask()
blend.inputImage = img; blend.maskImage = mask; blend.backgroundImage = CIImage.empty()
let out = blend.outputImage!.cropped(to: img.extent)
let ctx = CIContext()
try ctx.writePNGRepresentation(of: out, to: URL(fileURLWithPath: args[2]), format: .RGBA8, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!)
print("✓ recorte", args[2])
