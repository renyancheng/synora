import 'package:file_picker/file_picker.dart';
import 'package:image_picker/image_picker.dart';

import 'models.dart';

class AttachmentPicker {
  static Future<List<LocalAttachmentData>> pickFiles({List<String>? allowedExtensions}) async {
    final result = await FilePicker.platform.pickFiles(
      allowMultiple: true,
      withData: true,
      type: allowedExtensions == null ? FileType.any : FileType.custom,
      allowedExtensions: allowedExtensions,
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
    final image = await picker.pickImage(source: ImageSource.camera, imageQuality: 75);
    if (image == null) {
      return null;
    }
    final bytes = await image.readAsBytes();
    return LocalAttachmentData(fileName: image.name, bytes: bytes);
  }
}
