import 'package:flutter/material.dart';

import '../app_controller.dart';
import '../date_utils.dart';
import '../models.dart';
import '../strings.dart';

class MemoryPage extends StatefulWidget {
  const MemoryPage({super.key, required this.controller});

  final AppController controller;

  @override
  State<MemoryPage> createState() => _MemoryPageState();
}

class _MemoryPageState extends State<MemoryPage> {
  bool _loading = true;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => _load());
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      await widget.controller.refreshMemory();
    } finally {
      if (mounted) {
        setState(() => _loading = false);
      }
    }
  }

  Future<void> _deleteItem(MemoryItem item) async {
    final shouldDelete = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text(AppStrings.deleteMemoryTitle),
        content: const Text(AppStrings.deleteMemoryMessage),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text(AppStrings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text(AppStrings.delete),
          ),
        ],
      ),
    );
    if (shouldDelete != true) {
      return;
    }
    await widget.controller.deleteMemoryItem(item.id);
  }

  Future<void> _clearAll() async {
    final shouldClear = await showDialog<bool>(
      context: context,
      builder: (context) => AlertDialog(
        title: const Text(AppStrings.clearMemoryTitle),
        content: const Text(AppStrings.clearMemoryMessage),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(false),
            child: const Text(AppStrings.cancel),
          ),
          FilledButton(
            onPressed: () => Navigator.of(context).pop(true),
            child: const Text(AppStrings.clearMemory),
          ),
        ],
      ),
    );
    if (shouldClear != true) {
      return;
    }
    await widget.controller.clearAllMemory();
  }

  Future<void> _showDetails(MemoryItem item) async {
    await showDialog<void>(
      context: context,
      builder: (context) => AlertDialog(
        title: Text(item.title),
        content: SingleChildScrollView(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            mainAxisSize: MainAxisSize.min,
            children: <Widget>[
              Text('${AppStrings.lifecycleField}：${AppStrings.memoryTypeLabel(item.memoryType)}'),
              const SizedBox(height: 8),
              Text(item.content),
              const SizedBox(height: 12),
              Text('${AppStrings.createTimeField}：${formatDateTime(item.updatedAt)}'),
            ],
          ),
        ),
        actions: <Widget>[
          TextButton(
            onPressed: () => Navigator.of(context).pop(),
            child: const Text(AppStrings.confirmAction),
          ),
        ],
      ),
    );
  }

  @override
  Widget build(BuildContext context) {
    final summary = widget.controller.memorySummary;
    final items = widget.controller.memoryItems;
    return Scaffold(
      appBar: AppBar(
        title: const Text(AppStrings.memoryManagement),
        actions: <Widget>[
          IconButton(
            onPressed: items.isEmpty ? null : _clearAll,
            icon: const Icon(Icons.delete_sweep_outlined),
            tooltip: AppStrings.clearMemory,
          ),
        ],
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : RefreshIndicator(
              onRefresh: _load,
              child: ListView(
                padding: const EdgeInsets.all(16),
                children: <Widget>[
                  Card(
                    child: Padding(
                      padding: const EdgeInsets.all(16),
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: <Widget>[
                          Text(AppStrings.memorySummary, style: Theme.of(context).textTheme.titleMedium),
                          const SizedBox(height: 12),
                          Text(summary.trim().isEmpty ? AppStrings.emptyMemory : summary),
                        ],
                      ),
                    ),
                  ),
                  const SizedBox(height: 12),
                  Text(AppStrings.memoryList, style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(height: 8),
                  if (items.isEmpty)
                    const Padding(
                      padding: EdgeInsets.only(top: 12),
                      child: Text(AppStrings.emptyMemory),
                    )
                  else
                    ...items.map(
                      (item) => Card(
                        child: ListTile(
                          title: Text(item.title),
                          subtitle: Text(
                            '${AppStrings.memoryTypeLabel(item.memoryType)} · ${formatDateTime(item.updatedAt)}\n${item.content}',
                            maxLines: 3,
                            overflow: TextOverflow.ellipsis,
                          ),
                          isThreeLine: true,
                          onTap: () => _showDetails(item),
                          trailing: IconButton(
                            onPressed: () => _deleteItem(item),
                            icon: const Icon(Icons.delete_outline),
                            tooltip: AppStrings.delete,
                          ),
                        ),
                      ),
                    ),
                ],
              ),
            ),
    );
  }
}

