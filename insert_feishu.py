#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import re

# 读取原文件
with open('reply_server.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 读取要插入的代码
with open('_temp_feishu_api.txt', 'r', encoding='utf-8') as f:
    feishu_code = f.read()

# 找到插入位置（文件末尾的注释之前）
insert_marker = '''# 移除自动启动，由Start.py或手动启动
# if __name__ == "__main__":
#     uvicorn.run(app, host="0.0.0.0", port=8080)'''

if insert_marker in content:
    new_content = content.replace(insert_marker, feishu_code + '\n' + insert_marker)
    with open('reply_server.py', 'w', encoding='utf-8') as f:
        f.write(new_content)
    print('Feishu API endpoints added successfully')
else:
    print('Insert marker not found')
