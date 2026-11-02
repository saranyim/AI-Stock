import pandas as pd

# ดาวน์โหลดข้อมูลและบันทึกลงไฟล์ CSV ในเครื่อง
url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv"
df = pd.read_csv(url)

# บันทึกเก็บไว้เป็นไฟล์ Local
df.to_csv("sp500_companies.csv", index=False)
print("บันทึกไฟล์ sp500_companies.csv เรียบร้อยแล้ว")