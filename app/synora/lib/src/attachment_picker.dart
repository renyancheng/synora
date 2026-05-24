import 'package:file_picker/file_picker.dart';
import 'package:image_picker/image_picker.dart';

import 'models.dart';


class AttachmentPicker {
  static Future<List<LocalAttachmentData>> pickGalleryImages() async {
    final result = await FilePicker.pickFiles(
      allowMultiple: true,
      withData: true,
      type: FileType.custom,
      allowedExtensions: <String>['png', 'jpg', 'jpeg', 'webp'],
    );
    if (result == null) {
      return <LocalAttachmentData>[];
    }
    return result.files
        .where((file) => file.bytes != null)
        .map((file) => LocalAttachmentData(fileName: file.name, bytes: file.bytes!))
        .toList();
  }

  static Future<List<LocalAttachmentData>> pickFiles() async {
    final result = await FilePicker.pickFiles(
      allowMultiple: true,
      withData: true,
      type: FileType.custom,
      allowedExtensions: <String>['png', 'jpg', 'jpeg', 'webp', 'pdf', 'txt', 'json', 'csv', 'md'],
    );
    if (result == null) {
      return <LocalAttachmentData>[];
    }
    return result.files
        .where((file) => file.bytes != null)
        .map((file) => LocalAttachmentData(fileName: file.name, bytes: file.bytes!))
        .toList();
  }

  static Future<LocalAttachmentData?> pickPhoto() async {
    final picker = ImagePicker();
    final image = await picker.pickImage(source: ImageSource.camera, imageQuality: 80);
    if (image == null) {
      return null;
    }
    final bytes = await image.readAsBytes();
    return LocalAttachmentData(fileName: image.name, bytes: bytes);
  }
}
