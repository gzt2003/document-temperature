# document-temperature

实验室温度监测页面，用于通过 GitHub Pages 展示冷云平台温度数据。

## 文件

- `index.html`: 交互式温度曲线页面
- `temperature_data.json`: 页面读取的数据文件
- `.nojekyll`: GitHub Pages 静态站点标记

## GitHub Pages

在仓库设置中启用 Pages：

1. Settings -> Pages
2. Source: Deploy from a branch
3. Branch: `main`
4. Folder: `/root`
5. Save

页面地址通常为：

https://gzt2003.github.io/document-temperature/

## 数据更新

后续采集脚本只需要定期更新 `temperature_data.json`，页面刷新后即可看到最新温度曲线。
