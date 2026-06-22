# astrbot_plugin_base64_image_file

一个很小的 AstrBot 兼容插件：在消息发送前，把结果链里的 `base64://...` 图片保存成本地 PNG 文件，再替换成 `Image.fromFileSystem(...)`。

适合解决 KOOK 这类平台报错：

```text
不支持的文件资源类型: "base64://..."
```

## 用法

安装插件后重启 AstrBot 或重载插件即可，无需命令。

它会在 `on_decorating_result` 阶段自动处理其他插件返回的图片结果。
同时会给 KOOK 图片上传流程加一个兼容补丁，处理直接 `event.send(Image.fromBase64(...))` 的插件。

## 缓存清理

插件会把 base64 图片临时保存到 AstrBot 临时目录下的 `base64_image_file/`。

可在插件配置里调整：

- `图片最长保留小时数`：默认 24 小时。
- `最多保留图片数量`：默认 500 张。
- `清理间隔分钟数`：默认 10 分钟。

## 注意

- 主要处理 `event.get_result().chain` 里的 `Image(file="base64://...")`。
- 如果某个插件绕过结果链，直接调用底层接口发送消息，可能拦截不到。
- 生成的临时图片默认保存在 AstrBot 临时目录下的 `base64_image_file/`。
