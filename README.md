# astrbot_plugin_base64_image_file

一个很小的 AstrBot 兼容插件：在消息发送前，把结果链里的 `base64://...` 图片保存成本地 PNG 文件，再替换成 `Image.fromFileSystem(...)`。

适合解决 KOOK 这类平台报错：

```text
不支持的文件资源类型: "base64://..."
```

## 用法

安装插件后重启 AstrBot 或重载插件即可，无需命令。

它会在 `on_decorating_result` 阶段自动处理其他插件返回的图片结果。

## 注意

- 主要处理 `event.get_result().chain` 里的 `Image(file="base64://...")`。
- 如果某个插件绕过结果链，直接调用底层接口发送消息，可能拦截不到。
- 生成的临时图片默认保存在 AstrBot 临时目录下的 `base64_image_file/`。
