# 515光路测温云端采集器

该服务在无头 Chromium 中登录精创冷云，读取六台 GSP-8G 的历史数据，
只接受探头2/摄氏度记录，保存到 SQLite。程序默认处于试运行模式，只在
服务器生成 `temperature_data_preview.json`；明确启用发布后才会更新
GitHub Pages 使用的 `temperature_data.json`。

## 安全设计

- 冷云账号、密码和 GitHub PAT 只存放在服务器 `.env`。
- `.env`、SQLite、健康状态和调试数据均被 `.gitignore` 排除。
- Docker 容器不发布任何入站端口。
- GitHub PAT 只需目标仓库的 `Contents: Read and write` 权限。
- 首次发布前自动把旧版 `temperature_data.json` 归档到 `archive/`。
- `PUBLISH_ENABLED` 默认为 `false`，试运行不会向 GitHub 写入任何内容。

## 服务器目录

建议安装到 `/opt/temperature-monitor`。复制 `.env.example` 为 `.env`，
填写秘密后执行：

```bash
sudo docker compose build
sudo docker compose up -d
sudo docker compose logs -f --tail=100
```

首次验证建议只运行一轮，并保持 `.env` 中 `PUBLISH_ENABLED='false'`：

```bash
sudo docker compose run --rm -e RUN_ONCE=true collector
sudo docker compose down
```

采集结果保存在 `data/temperature_data_preview.json`，运行状态保存在
`data/health.json`。验证通过后再把 `PUBLISH_ENABLED` 改为 `true`。

## 本地解析器测试

```bash
python -m unittest discover -s tests -v
```

